# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/02_pp.ipynb.

# %% auto 0
__all__ = ['get_filtered_data', 'create_plot', 'e2e_plot', 'test_new_plot']

# %% ../nbs/02_pp.ipynb 3
import json, os
import itertools as it
from collections import defaultdict

import numpy as np
import pandas as pd
import polars as pl
import datetime as dt

from typing import List, Tuple, Dict, Union, Optional

import altair as alt

from salk_toolkit.plots import stk_plot, stk_deregister, matching_plots, get_plot_fn, get_plot_meta
from salk_toolkit.utils import *
from salk_toolkit.io import load_parquet_with_metadata, extract_column_meta, group_columns_dict, list_aliases, read_annotated_data, read_json

# %% ../nbs/02_pp.ipynb 6
# Augment each draw with bootstrap data from across whole population to make sure there are at least <threshold> samples
def augment_draws(data, factors=None, n_draws=None, threshold=50):
    if n_draws == None: n_draws = data.draw.max()+1
    
    if factors: # Run recursively on each factor separately and concatenate results
        if data[ ['draw']+factors ].value_counts().min() >= threshold: return data # This takes care of large datasets fast
        return data.groupby(factors).apply(augment_draws,n_draws=n_draws,threshold=threshold).reset_index(drop=True) # Slow-ish, but only needed on small data now
    
    # Get count of values for each draw
    draw_counts = data['draw'].value_counts() # Get value counts of existing draws
    if len(draw_counts)<n_draws: # Fill in completely missing draws
        draw_counts = (draw_counts + pd.Series(0,index=range(n_draws))).fillna(0).astype(int)
        
    # If no new draws needed, just return original
    if draw_counts.min()>=threshold: return data
    
    # Generate an index for new draws
    new_draws = [ d for d,c in draw_counts[draw_counts<threshold].items() for _ in range(threshold-c) ]

    # Generate new draws
    new_rows = data.iloc[np.random.choice(len(data),len(new_draws)),:].copy()
    new_rows['draw'] = new_draws
    
    return pd.concat([data, new_rows])

# %% ../nbs/02_pp.ipynb 7
# Get the categories that are in use
def get_cats(col, cats=None):
    if cats is None or len(set(col.dtype.categories)-set(cats))>0: cats = col.dtype.categories
    return [ c for c in cats if c in col.unique() ]

# %% ../nbs/02_pp.ipynb 8
# Get all data required for a given graph
# Only return columns and rows that are needed
# This can handle either a pandas DataFrame or a polars LazyDataFrame (to allow for loading only needed data)
def get_filtered_data(full_df, data_meta, pp_desc, columns=[]):
    
    # Figure out which columns we actually need
    meta_cols = ['weight', 'training_subsample', '__index_level_0__'] + (['draw'] if vod(get_plot_meta(pp_desc['plot']),'draws') else []) + columns
    cols = [ pp_desc['res_col'] ]  + vod(pp_desc,'factor_cols',[]) + list(vod(pp_desc,'filter',{}).keys())
    cols += [ c for c in meta_cols if c in full_df.columns and c not in cols ]
    
    # If any aliases are used, cconvert them to column names according to the data_meta
    gc_dict = group_columns_dict(data_meta)
    c_meta = extract_column_meta(data_meta)
    
    cols = [ c for c in np.unique(list_aliases(cols,gc_dict)) if c in full_df.columns ]
    
    #print("C",cols)
    
    lazy = isinstance(full_df,pl.LazyFrame)
    if lazy: pl.enable_string_cache() # Needed for categories to be comparable to strings
    
    df = full_df.select(cols) if lazy else full_df[cols]
    
    
    # Filter using demographics dict. This is very clever but hard to read. See:
    filter_dict = vod(pp_desc,'filter',{})
    inds = True if lazy else np.full(len(df),True) 
    for k, v in filter_dict.items():
        
        if isinstance(v,tuple) and (vod(c_meta[k],'continuous') or vod(c_meta[k],'datetime')): # Only special case where we actually need a range
            if lazy: inds = (((pl.col(k)>=v[0]) & (pl.col(k)<=v[1])) | pl.col(k).is_null()) & inds
            else: inds = (((df[k]>=v[0]) & (df[k]<=v[1])) | df[k].isna()) & inds
            continue # NB! this approach does not work for ordered categoricals with polars LazyDataFrame, hence handling that separately below
        
        # Filter by list of values:
        if isinstance(v,tuple):
            if vod(c_meta[k],'categories','infer')=='infer': raise Exception(f'Ordering unknown for column {k}')
            cats = list(c_meta[k]['categories'])
            if set(v) & set(cats) != set(v): raise Exception(f'Column {k} values {v} not found in {cats}')
            bi, ei = cats.index(v[0]), cats.index(v[1])
            flst = cats[bi:ei+1] # 
        elif 'groups' in c_meta[k] and v in c_meta[k]['groups']:
            flst = c_meta[k]['groups'][v]
        elif isinstance(v,list): flst = v # List indicates a set of values
        else: flst = [v] # Just filter on single value    
            
        inds =  (pl.col(k).is_in(flst) if lazy else df[k].isin(flst)) & inds
            
    filtered_df = df.filter(inds).collect().to_pandas() if lazy else df[inds].copy()
    if lazy and '__index_level_0__' in filtered_df.columns: # Fix index, if provided. This is a hack but seems to be needed as polars does not handle index properly by default
        filtered_df.index = filtered_df['__index_level_0__'] 
    
    # Replace draw with the draws used in modelling - NB! does not currenlty work for group questions
    if 'draw' in filtered_df.columns and pp_desc['res_col'] in vod(data_meta,'draws_data',{}):
        uid, ndraws = data_meta['draws_data'][pp_desc['res_col']]
        filtered_df = deterministic_draws(filtered_df, ndraws, uid, n_total = data_meta['total_size'] )
    
    # If not poststratisfied
    if not vod(pp_desc,'poststrat',True):
        filtered_df = filtered_df.assign(weight = 1.0) # Remove weighting
        if 'training_subsample' in filtered_df.columns:
            filtered_df = filtered_df[filtered_df['training_subsample']]
    
    n_datapoints = len(filtered_df)
    
    # If res_col is a group of questions
    # This might move to wrangle but currently easier to do here as we have gc_dict handy
    if pp_desc['res_col'] in gc_dict:
        value_vars = [ c for c in gc_dict[pp_desc['res_col']] if c in cols ]
        id_vars = [ c for c in cols if c not in value_vars ]
        filtered_df = filtered_df.melt(id_vars=id_vars, value_vars=value_vars, var_name='question', value_name=pp_desc['res_col'])
        filtered_df['question'] = pd.Categorical(filtered_df['question'],gc_dict[pp_desc['res_col']])
    
    # Convert ordered categorical to continuous if we can
    res_meta = c_meta[pp_desc['res_col']]
    if vod(pp_desc,'convert_res') == 'continuous' and vod(res_meta,'ordered') and vod(res_meta,'categories','infer') != 'infer':
        cmap = dict(zip(res_meta['categories'],vod(res_meta,'num_values',range(len(res_meta['categories'])))))
        filtered_df[pp_desc['res_col']] = pd.to_numeric(filtered_df[pp_desc['res_col']].replace(cmap))
        
    # Filter out the unused categories so plots are cleaner
    for k in filtered_df.columns:
        if filtered_df[k].dtype.name == 'category':
            m_cats = c_meta[k]['categories'] if vod(c_meta[k],'categories','infer')!='infer' else None
            filtered_df.loc[:,k] = pd.Categorical(filtered_df[k],get_cats(filtered_df[k],m_cats),ordered=vod(c_meta[k],'ordered',False))
    
    # Aggregate the data into right shape
    pparams = wrangle_data(filtered_df, data_meta, pp_desc)
    
    # How many datapoints the plot is based on. This is useful metainfo to display sometimes
    pparams['n_datapoints'] = n_datapoints
    
    if lazy: pl.disable_string_cache()
    
    return pparams

# %% ../nbs/02_pp.ipynb 9
# Groupby if needed - this simplifies the wrangle considerably :)
def gb_in(df, gb_cols):
    return df.groupby(gb_cols) if len(gb_cols)>0 else df

def discretize_continuous(col, col_meta={}):
    # NB! qcut might be a better default - see where testing leads us
    cut = pd.cut(col, bins = vod(col_meta,'bins',5), labels = vod(col_meta,'bin_labels',None) )
    cut = pd.Categorical(cut.astype(str), map(str,cut.dtype.categories), True) # Convert from intervals to strings for it to play nice with altair
    return cut

# Helper function that handles reformating data for create_plot
def wrangle_data(raw_df, data_meta, pp_desc):
    
    plot_meta = get_plot_meta(pp_desc['plot'])
    col_meta = extract_column_meta(data_meta)
    
    res_col, factor_cols = vod(pp_desc,'res_col'), vod(pp_desc,'factor_cols')
    
    draws, continuous, data_format = (vod(plot_meta, n, False) for n in ['draws','continuous','data_format'])
    
    gb_dims = (['draw'] if draws else []) + (factor_cols if factor_cols else []) + (['question'] if 'question' in raw_df.columns else [])
    
    if 'weight' not in raw_df.columns: raw_df = raw_df.assign(weight=1.0) # This also works for empty df-s
    else: raw_df.loc[:,'weight'] = raw_df['weight'].fillna(1.0)

    if draws and 'draw' in raw_df.columns and 'augment_to' in pp_desc: # Should we try to bootstrap the data to always have augment_to points. Note this is relatively slow
        raw_df = augment_draws(raw_df,gb_dims[1:],threshold=pp_desc['augment_to'])
        
    pparams = { 'value_col': 'value' }
    data = None
    
    if data_format=='raw':
        pparams['value_col'] = res_col
        if vod(plot_meta,'sample'):
            data = gb_in(raw_df[gb_dims+[res_col]],gb_dims).sample(plot_meta['sample'],replace=True)
        else: data = raw_df[gb_dims+[res_col]]

    elif False and data_format=='table': # TODO: Untested. Fix when first needed
        ddf = pd.get_dummies(raw_df[res_col])
        res_cols = list(ddf.columns)
        ddf.loc[:,gb_dims] = raw_df[gb_dims]
        data = gb_in(ddf,gb_dims)[res_cols].mean().reset_index()
        
    elif data_format=='longform':
        rc_meta = vod(col_meta,res_col,{})
        if raw_df[res_col].dtype == 'category':  #'categories' in rc_meta: # categorical
            pparams['cat_col'] = res_col 
            pparams['value_col'] = 'percent'
            data = (raw_df.groupby(gb_dims+[res_col])['weight'].sum()/gb_in(raw_df,gb_dims)['weight'].sum()).rename(pparams['value_col']).dropna().reset_index()
            
        else: # Continuous
            agg_fn = vod(pp_desc,'agg_fn','mean') # We may want to try median vs mean or plot sd-s or whatever
            agg_fn = vod(plot_meta,'agg_fn',agg_fn) # Some plots mandate this value (election model for instance)
            data = getattr(gb_in(raw_df,gb_dims)[res_col],agg_fn)().dropna().reset_index() 
            pparams['value_col'] = res_col
            
        if vod(plot_meta,'group_sizes'):
            data = data.merge(gb_in(raw_df,gb_dims).size().rename('group_size').reset_index(),on=gb_dims,how='left')
    else:
        raise Exception("Unknown data_format")
        
    # Ensure all rv columns other than value are categorical
    for c in data.columns:
        if c in ['group_size']: continue # bypass some columns added above
        if data[c].dtype.name != 'category' and c!=pparams['value_col']:
            if vod(vod(col_meta,c,{}),'continuous'):
                data.loc[:,c] = discretize_continuous(data[c],vod(col_meta,c,{}))
            else: # Just assume it's categorical by any other name
                data.loc[:,c] = pd.Categorical(data[c])
            
    pparams['data'] = data
    return pparams

# %% ../nbs/02_pp.ipynb 11
# Create a color scale
ordered_gradient = ["#c30d24", "#f3a583", "#94c6da", "#1770ab"]
def meta_color_scale(cmeta,argname='colors',column=None):
    scale = vod(cmeta,argname)
    cats = column.dtype.categories if column.dtype.name=='category' else None
    if scale is None and column is not None and column.dtype.name=='category' and column.dtype.ordered:
        scale = dict(zip(cats,gradient_to_discrete_color_scale(ordered_gradient, len(cats))))
    return to_alt_scale(scale,cats)

# %% ../nbs/02_pp.ipynb 12
# Function that takes filtered raw data and plot information and outputs the plot
# Handles all of the data wrangling and parameter formatting
def create_plot(pparams, data_meta, pp_desc, alt_properties={}, dry_run=False, width=200, return_matrix_of_plots=False):
    
    data = pparams['data']

    plot_meta = get_plot_meta(pp_desc['plot'])
    col_meta = extract_column_meta(data_meta)
    
    if 'plot_args' in pp_desc: pparams.update(pp_desc['plot_args'])
    pparams['color_scale'] = meta_color_scale(col_meta[pp_desc['res_col']],'colors',data[pp_desc['res_col']])
    if data[pp_desc['res_col']].dtype.name=='category':
        pparams['cat_order'] = list(data[pp_desc['res_col']].dtype.categories) 
        
    pparams['val_format'] = '.1%' if pparams['value_col'] == 'percent' else '.1f'

    # Handle factor columns 
    factor_cols = vod(pp_desc,'factor_cols',[])
    
    # If we have a question column not handled by the plot, add it to factors:
    if 'question' in data.columns and not vod(plot_meta,'question'):
        factor_cols = factor_cols + ['question']
    # If we don't have a question column but need it, just fill it with res_col name
    elif 'question' not in data.columns and vod(plot_meta,'question'):
        data.loc[:,'question'] = pd.Categorical([pp_desc['res_col']]*len(pparams['data']))
        
    if vod(plot_meta,'question'):
        pparams['question_color_scale'] = meta_color_scale(col_meta[pp_desc['res_col']],'question_colors',data['question'])
        pparams['question_order'] = list(data['question'].dtype.categories) 
    
    if vod(plot_meta,'continuous') and 'cat_col' in pparams:
        to_ind = 1 if len(factor_cols)>0 and vod(pp_desc,'internal_facet') else 0
        factor_cols = factor_cols.copy()
        factor_cols.insert(to_ind,pparams['cat_col'])
    
    if factor_cols:
        # See if we should use it as an internal facet?
        plot_args = vod(pp_desc,'plot_args',{})
        if vod(pp_desc,'internal_facet'):
            pparams['factor_col'] = factor_cols[0]
            if factor_cols[0] == 'question':
                pparams['factor_color_scale'] = meta_color_scale(col_meta[pp_desc['res_col']],'question_colors',data['question'])
            else:
                pparams['factor_color_scale'] = meta_color_scale(col_meta[factor_cols[0]],'colors',data[factor_cols[0]])
            pparams['factor_order'] = list(data[factor_cols[0]].dtype.categories) 
            factor_cols = factor_cols[1:] # Leave rest for external faceting
            if 'factor_meta' in plot_meta: 
                for kw in plot_meta['factor_meta']: pparams[kw] = vod(col_meta[pparams['factor_col']],kw)
        
        # If we still have more than 1 factor - merge the rest
        if len(factor_cols)>1:
            n_facet_cols = len(data[factor_cols[-1]].dtype.categories)
            if not return_matrix_of_plots:
                factor_col = '+'.join(factor_cols)
                data.loc[:,factor_col] = data[factor_cols].agg(', '.join, axis=1)
                pparams['data'] = data
                n_facet_cols = len(data[factor_cols[-1]].dtype.categories)
                factor_cols = [factor_col]
        else:
            n_facet_cols = vod(plot_meta,'factor_columns',1)
    
    plot_fn = get_plot_fn(pp_desc['plot'])
            
    
    # Create the plot using it's function
    if dry_run: return pparams

    if factor_cols: n_facet_cols = vod(plot_args,'n_facet_cols',n_facet_cols) # Allow plot_args to override col nr
    dims = {'width': width//n_facet_cols if factor_cols else width}
    if 'aspect_ratio' in plot_meta:   dims['height'] = int(dims['width']/plot_meta['aspect_ratio'])        
    
    # Make plot properties available to plot function (mostly useful for as_is plots)
    pparams.update(dims); pparams['alt_properties'] = alt_properties; pparams['outer_factors'] = factor_cols
    
    # Trim down parameters list if needed
    pparams = clean_kwargs(plot_fn,pparams)
    
    if vod(plot_meta,'as_is'): # if as_is set, just return the plot as-is
        return plot_fn(**pparams)
    elif factor_cols:
        if return_matrix_of_plots: 
            del pparams['data']
            combs = it.product( *[data[fc].dtype.categories for fc in factor_cols ])
            #print( [ data[(data[factor_cols]==c).all(axis=1)] for c in combs ] )
            #print(list(combs))
            return list(batch([
                plot_fn(data[(data[factor_cols]==c).all(axis=1)],**pparams).properties(title='-'.join(map(str,c)),**dims, **alt_properties)
                for c in combs
                ], n_facet_cols))
        else: # Use faceting:
            plot = plot_fn(**pparams).properties(**dims, **alt_properties).facet(f'{factor_cols[0]}:O',columns=n_facet_cols)
    else:
        plot = plot_fn(**pparams).properties(**dims, **alt_properties)
        if return_matrix_of_plots: plot = [[plot]]

    return plot

# %% ../nbs/02_pp.ipynb 16
# A convenience function to draw a plot straight from a dataset
def e2e_plot(pp_desc, data_file=None, full_df=None, data_meta=None, width=800, check_match=True,**kwargs):
    if data_file is None and full_df is None:
        raise Exception('Data must be provided either as data_file or full_df')
    if data_file is None and data_meta is None:
        raise Exception('If data provided as full_df then data_meta must also be given')
        
    if full_df is None: 
        if data_file.endswith('.parquet'): # Try lazy loading as it only loads what it needs from disk
            full_df, full_meta = load_parquet_with_metadata(data_file,lazy=True)
            dm = full_meta['data']
        else: full_df, dm = read_annotated_data(data_file)
        if data_meta is None: data_meta = dm
    
    matches = matching_plots(pp_desc, full_df, data_meta, details=True, list_hidden=True)
    
    if pp_desc['plot'] not in matches: 
        raise Exception(f"Plot not registered: {pp_desc['plot']}")
    
    fit, imp = matches[pp_desc['plot']]
    if  fit<0:
        raise Exception(f"Plot {pp_desc['plot']} not applicable in this situation because of flags {imp}")
        
    pparams = get_filtered_data(full_df, data_meta, pp_desc)
    return create_plot(pparams, data_meta, pp_desc, width=width,**kwargs)

# Another convenience function to simplify testing new plots
def test_new_plot(fn, pp_desc, *args, plot_meta={}, **kwargs):
    stk_plot(**{**plot_meta,'plot_name':'test'})(fn) # Register the plot under name 'test'
    pp_desc = {**pp_desc, 'plot': 'test'}
    res = e2e_plot(pp_desc,*args,**kwargs)
    stk_deregister('test') # And de-register it again
    return res
