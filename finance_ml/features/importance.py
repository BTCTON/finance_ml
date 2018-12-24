import pandas as pd
import numpy as np
from sklearn.metrics import log_loss, accuracy_score
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import BaggingClassifier

from finance_ml.multiprocessing import mp_pandas_obj
from finance_ml.model_selection import PurgedKFold, cv_score


def feat_imp_MDI(forest, feat_names):
    """Compute Mean Decrease Impurity
    
    Params
    ------
    forest: Forest Classifier instance
    feat_names: list(str)
        List of names of features

    Returns
    -------
    imp: pd.DataFrame
        Importance means and standard deviations
    """
    imp_dict = {i: tree.feature_importances_ for i, tree in
                enumerate(forest.estimators_)}
    imp_df = pd.DataFrame.from_dict(imp_dict, orient='index')
    imp_df.columns = feat_names
    # 0 simply means not used for splitting
    imp_df = imp_df.replace(0, np.nan)
    imp = pd.concat({'mean': imp_df.mean(),
                     'std': imp_df.std() * np.sqrt(imp_df.shape[0])},
                    axis=1)
    imp /= imp['mean'].sum()
    return imp


def feat_imp_MDA(clf, X, y, n_splits, t1, sample_weight=None,
                 pct_embargo=0, scoring='neg_log_loss'):
    """Calculate Mean Decrease Accuracy
    
    Params
    ------
    clf: Classifier instance
    X: pd.DataFrame, Input feature
    y: pd.Series, Label
    n_splits: int
        The number of splits for cross validation
    sample_weight: array-like
        Sampling weight for fit function
    t1: pd.Series
        Index and values correspond to begenning and end of timestamps for each point
    pct_embargo: float
        The ratio to get rid of from  data
    
    Returns
    -------
    imp: pd.DataFrame, feature importance of means and standard deviations
    scores: float, scores of cross validation
    """
    if scoring not in ['neg_log_loss', 'accuracy']:
        raise Exception('wrong scoring method')
    cv_gen = PurgedKFold(n_splits=n_splits, t1=t1, pct_embargo=pct_embargo)
    index = np.arange(n_splits)
    scores = pd.Series(index=index)
    scores_perm = pd.DataFrame(index=index, columns=X.columns)
    for idx, (train, test) in zip(index, cv_gen.split(X=X)):
        X_train = X.iloc[train]
        y_train = y.iloc[train]
        if sample_weight is not None:
            w_train = sample_weight.iloc[train].values
        else:
            w_train = None
        X_test = X.iloc[test]
        y_test = y.iloc[test]
        if sample_weight is not None:
            w_test = sample_weight.iloc[test].values
        else:
            w_test = None
        clf_fit = clf.fit(X_train, y_train, sample_weight=w_train)
        if scoring == 'neg_log_loss':
            prob = clf_fit.predict_proba(X_test)
            scores.loc[idx] = -log_loss(y_test, prob,
                                        sample_weight=w_test,
                                        labels=clf_fit.classes_)
        else:
            pred = clf_fit.predict(X_test)
            scores.loc[idx] = accuracy_score(y_test, pred,
                                             sample_weight=w_test)

        for col in X.columns:
            X_test_ = X_test.copy(deep=True)
            # Randomize certain feature to make it not effective
            np.random.shuffle(X_test_[col].values)
            if scoring == 'neg_log_loss':
                prob = clf_fit.predict_proba(X_test_)
                scores_perm.loc[idx, col] = -log_loss(y_test, prob,
                                                      sample_weight=w_test,
                                                      labels=clf_fit.classes_)
            else:
                pred = clf_fit.predict(X_test_)
                scores_perm.loc[idx, col] = accuracy_score(y_test, pred,
                                                           sample_weight=w_test)
    # (Original score) - (premutated score)
    imprv = (-scores_perm).add(scores, axis=0)
    # Relative to maximum improvement
    if scoring == 'neg_log_loss':
        max_imprv = -scores_perm
    else:
        max_imprv = 1. - scores_perm
    imp = imprv / max_imprv
    imp = pd.DataFrame(
        {'mean': imp.mean(), 'std': imp.std() * np.sqrt(imp.shape[0])})
    return imp, scores.mean()


def feat_imp_SFI(clf, X, y, sample_weight=None, scoring='neg_log_loss',
                 n_splits=3, t1=None, cv_gen=None, pct_embargo=0, purging=True):
    """Calculate Single Feature Importance
    
    Params
    ------
    clf: Classifier instance
    X: pd.DataFrame
    y: pd.Series, optional
    sample_weight: pd.Series, optional
        If specified, apply this to bot testing and training
    scoring: str, default 'neg_log_loss'
        The name of scoring methods. 'accuracy' or 'neg_log_loss'
    
    n_splits: int, default 3
        The number of splits for cross validation
    t1: pd.Series
        Index and value correspond to the begining and end of information
    cv_gen: KFold instance
        If not specified, use PurgedKfold
    pct_embargo: float, default 0
        The percentage of applying embargo
    purging: bool, default True
        If true, apply purging method
        
    Returns
    -------
    imp: pd.DataFrame, feature importance of means and standard deviations
    """
    imp = pd.DataFrame(columns=['mean', 'std'])
    for feat_name in X.columns:
        scores = cv_score(clf, X=X[[feat_name]], y=y,
                          sample_weight=sample_weight,
                          scoring=scoring,
                          cv_gen=cv_gen,
                          n_splits=n_splits,
                          t1=t1,
                          pct_embargo=pct_embargo,
                          purging=purging)
        imp.loc[feat_name, 'mean'] = scores.mean()
        imp.loc[feat_name, 'std'] = scores.std() * np.sqrt(scores.shape[0])
    return imp


def feat_importance(X, cont, clf=None, n_estimators=1000, n_splits=10, max_samples=1.,
                    num_threads=24, pct_embargo=0., scoring='accuracy',
                    method='SFI', min_w_leaf=0., **kwargs):
    n_jobs = (-1 if num_threads > 1 else 1)
    # Build classifiers
    if clf is None:
        base_clf = DecisionTreeClassifier(criterion='entropy', max_features=1,
                                          class_weight='balanced',
                                          min_weight_fraction_leaf=min_w_leaf)
        clf = BaggingClassifier(base_estimator=base_clf, n_estimators=n_estimators,
                                max_features=1., max_samples=max_samples,
                                oob_score=True, n_jobs=n_jobs)
    fit_clf = clf.fit(X, cont['size'], sample_weight=cont['w'].values)
    if hasattr(fit_clf, 'oob_score_'):
        oob = fit_clf.oob_score_
    else:
        oob = None
    if method == 'MDI':
        imp = feat_imp_MDI(fit_clf, feat_names=X.columns)
        oos = cv_score(clf, X=X, y=cont['size'], n_splits=n_splits,
                       sample_weight=cont['w'], t1=cont['t1'],
                       pct_embargo=pct_embargo, scoring=scoring).mean()
    elif method == 'MDA':
        imp, oos = feat_imp_MDA(clf, X=X, y=cont['size'], n_splits=n_splits,
                                sample_weight=cont['w'], t1=cont['t1'],
                                pct_embargo=pct_embargo, scoring=scoring)
    elif method == 'SFI':
        cv_gen = PurgedKFold(n_splits=n_splits, t1=cont['t1'], pct_embargo=pct_embargo)
        oos = cv_score(clf, X=X, y=cont['size'], sample_weight=cont['w'],
                       scoring=scoring, cv_gen=cv_gen)
        clf.n_jobs = 1
        imp = mp_pandas_obj(feat_imp_SFI, ('feat_names', X.columns),
                            num_threads, clf=clf, X=X, cont=cont,
                            scoring=scoring, cv_gen=cv_gen)
    return imp, oob, oos