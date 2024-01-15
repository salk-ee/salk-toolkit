# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/10_utils.ipynb.

# %% auto 0
__all__ = ['warn', 'default_color', 'vod', 'factorize_w_codes', 'batch', 'loc2iloc', 'match_sum_round', 'min_diff', 'continify',
           'match_data', 'replace_constants', 'index_encoder', 'to_alt_scale', 'multicol_to_vals_cats',
           'gradient_to_discrete_color_scale', 'is_datetime', 'rel_wave_times', 'stable_draws', 'deterministic_draws']

# %% ../nbs/10_utils.ipynb 3
import json, os, warnings, math
import itertools as it
from collections import defaultdict

import numpy as np
import pandas as pd
import datetime as dt

import altair as alt
import matplotlib.colors as mpc
from copy import deepcopy
from hashlib import sha256

from typing import List, Tuple, Dict, Union, Optional

# %% ../nbs/10_utils.ipynb 4
# Value or Default - returns key value in dict if key in dict, otherwise Mone
def vod(d,k,default=None): return d[k] if k in d else default

# %% ../nbs/10_utils.ipynb 5
# convenience for warnings that gives a more useful stack frame (fn calling the warning, not warning fn itself)
warn = lambda msg,*args: warnings.warn(msg,*args,stacklevel=3)

# %% ../nbs/10_utils.ipynb 6
# I'm surprised pandas does not have this function but I could not find it. 
def factorize_w_codes(s, codes):
    res = s.replace(dict(zip(codes,range(len(codes)))))
    if not s.isin(codes).all(): # Throw an exception if all values were not replaced
        vals = set(s) - set(codes)
        raise Exception(f'Codes for {s.name} do not match all values: {vals}')
    return res.to_numpy(dtype='int')

# %% ../nbs/10_utils.ipynb 7
# Simple batching of an iterable
def batch(iterable, n=1):
    l = len(iterable)
    for ndx in range(0, l, n):
        yield iterable[ndx:min(ndx + n, l)]

# %% ../nbs/10_utils.ipynb 8
# turn index values into order indices
def loc2iloc(index, vals):
    d = dict(zip(np.array(index),range(len(index))))
    return [ d[v] for v in vals ]

# %% ../nbs/10_utils.ipynb 9
# Round in a way that preserves total sum
def match_sum_round(s):
    s = np.array(s)
    fs = np.floor(s)
    diff = round(s.sum()-fs.sum())
    residues = np.argsort(-(s%1))[:diff]
    fs[residues] = fs[residues]+1
    return fs.astype('int')

# %% ../nbs/10_utils.ipynb 11
# Find the minimum difference between two values in the array
def min_diff(arr):
    b = np.diff(np.sort(arr))
    if len(b)==0 or b.max()==0.0: return 0
    else: return b[b>0].min()

# Turn a discretized variable into a more smooth continuous one w a gaussian kernel
def continify(ar, bounded=False):
    mi,ma = ar.min(), ar.max()
    noise = np.random.normal(0,0.5 * min_diff(ar),size=len(ar))
    res = ar + noise
    if bounded: # Reflect the noise on the boundaries
        res[res>ma] = ma - (res[res>ma] - ma)
        res[res<mi] = mi + (mi - res[res<mi])
    return res

# %% ../nbs/10_utils.ipynb 13
from scipy.spatial.distance import cdist
from scipy.optimize import linear_sum_assignment

# %% ../nbs/10_utils.ipynb 14
# Match data1 with data2 on columns cols as closely as possible
def match_data(data1,data2,cols=None):
    d1 = data1[cols].copy().dropna()
    d2 = data2[cols].copy().dropna()

    ccols = [c for c in cols if d1[c].dtype.name=='category']
    for c in ccols: # replace categories with their index. This is ok for ordered categories, not so great otherwise
        s1, s2 = set(d1[c].dtype.categories), set(d2[c].dtype.categories)
        if s1-s2 and s2-s1: # one-way imbalance is fine
            raise Exception(f"Categorical columns differ in their categories on: {s1-s2} vs {s2-s1}")
        
        md = d1 if len(s2-s1)==0 else d2
        mdict = dict(zip(md[c].dtype.categories, range(len(md[c].dtype.categories))))
        d1[c] = d1[c].replace(mdict)
        d2[c] = d2[c].replace(mdict)

    dmat = cdist(d1, d2, 'mahalanobis')
    i1, i2 = linear_sum_assignment(dmat, maximize=False)
    ind1, ind2 = d1.index[i1], d2.index[i2]
    return ind1, ind2

# %% ../nbs/10_utils.ipynb 16
# Allow 'constants' entries in the dict to provide replacement mappings
# This leads to much more readable jsons as repetitions can be avoided
def replace_constants(d, constants = {}, inplace=False):
    if not inplace: d = deepcopy(d)
    if type(d)==dict and 'constants' in d:
        constants = constants.copy() # Otherwise it would propagate back up through recursion - see test6 below
        constants.update(d['constants'])
        del d['constants']

    for k, v in (d.items() if type(d)==dict else enumerate(d)):
        if type(v)==str and v in constants:
            d[k] = constants[v]
        elif type(v)==dict or type(v)==list:
            d[k] = replace_constants(v,constants, inplace=True)
            
    return d

# %% ../nbs/10_utils.ipynb 18
# JSON encoder needed to convert pandas indices into lists for serialization
def index_encoder(z):
    if isinstance(z, pd.Index):
        return list(z)
    else:
        type_name = z.__class__.__name__
        raise TypeError(f"Object of type {type_name} is not serializable")

# %% ../nbs/10_utils.ipynb 19
default_color = 'lime' # Something that stands out so it is easy to notice a missing color

# Helper function to turn a dictionary into an Altair scale (or None into alt.Undefined)
# Also: preserving order matters because scale order overrides sort argument
def to_alt_scale(scale, order=None):
    if scale is None: scale = alt.Undefined
    if isinstance(scale,dict):
        if order is None: order = scale.keys()
        #else: order = [ c for c in order if c in scale ]
        scale = alt.Scale(domain=list(order),range=[ (scale[c] if c in scale else default_color) for c in order ])
    return scale

# %% ../nbs/10_utils.ipynb 20
# Turn a question with multiple variants all of which are in distinct columns into a two columns - one with response, the other with which question variant was used

def multicol_to_vals_cats(df, cols=None, col_prefix=None, reverse_cols=[], reverse_suffixes=None, cat_order=None, vals_name='vals', cats_name='cats', inplace=False):
    if not inplace: df = df.copy()
    if cols is None: cols = [ c for c in df.columns if c.startswith(col_prefix)]
    
    if not reverse_cols and reverse_suffixes is not None:
        reverse_cols = list({ c for c in cols for rs in reverse_suffixes if c.endswith(rs)})
    
    if len(reverse_cols)>0:
        #print("RC",reverse_cols)
        remap = dict(zip(cat_order,reversed(cat_order)))
        df.loc[:,reverse_cols] = df.loc[:,reverse_cols].replace(remap)
    
    tdf = df[cols]
    cinds = np.argmax(tdf.notna(),axis=1)
    df.loc[:,vals_name] = np.array(tdf)[range(len(tdf)),cinds]
    df.loc[:,cats_name] = np.array(tdf.columns)[cinds]
    return df

# %% ../nbs/10_utils.ipynb 22
# Grad is a list of colors
def gradient_to_discrete_color_scale( grad, num_colors):
    cmap = mpc.LinearSegmentedColormap.from_list('grad',grad)
    return [mpc.to_hex(cmap(i)) for i in np.linspace(0, 1, num_colors)]

# %% ../nbs/10_utils.ipynb 24
def is_datetime(col):
    with warnings.catch_warnings():
        warnings.simplefilter(action='ignore', category=UserWarning)
        return pd.api.types.is_datetime64_any_dtype(col) or (col.dtype.name in ['str','object'] and pd.to_datetime(col,errors='coerce').notna().any())

# %% ../nbs/10_utils.ipynb 25
# Convert a series of wave indices and a series of survey dates into a time series usable by our gp model
def rel_wave_times(ws, dts, dt0=None):
    df = pd.DataFrame({'wave':ws, 'dt': pd.to_datetime(dts)})
    adf = df.groupby('wave')['dt'].median()
    if dt0 is None: dt0 = adf.max() # use last wave date as the reference
    
    w_to_time = dict(((adf - dt0).dt.days/30).items())
    
    return pd.Series(df['wave'].replace(w_to_time),name='t')

# %% ../nbs/10_utils.ipynb 27
# Generate a random draws column that is deterministic in n, n_draws and uid
def stable_draws(n, n_draws, uid):
    # Initialize a random generator with a hash of uid
    bgen = np.random.SFC64(np.frombuffer(sha256(str(uid).encode("utf-8")).digest(), dtype='uint32'))
    gen = np.random.Generator(bgen)
    
    n_samples = int(math.ceil(n/n_draws))
    draws = (list(range(n_draws))*n_samples)[:n]
    return gen.permuted(draws)

# Use the stable_draws function to deterministicall assign shuffled draws to a df 
def deterministic_draws(df, n_draws, uid, n_total=None):
    if n_total is None: n_total = len(df)
    df.loc[:,'draw'] = pd.Series(stable_draws(n_total, n_draws, uid), index = np.arange(n_total))
    return df
