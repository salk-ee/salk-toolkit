# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/03_plots.ipynb.

# %% auto 0
__all__ = ['registry', 'registry_meta', 'stk_plot_defaults', 'priority_weights', 'stk_plot', 'stk_deregister', 'get_plot_fn',
           'get_plot_meta', 'calculate_priority', 'calculate_impossibilities', 'matching_plots',
           'register_stk_cont_version', 'boxplots', 'columns', 'diff_columns', 'make_start_end', 'likert_bars',
           'kde_1d', 'density', 'matrix', 'lines', 'area_smooth', 'likert_aggregate', 'likert_rad_pol', 'geoplot']

# %% ../nbs/03_plots.ipynb 3
import json, os, inspect
import itertools as it
from collections import defaultdict

import numpy as np
import pandas as pd
import datetime as dt

from typing import List, Tuple, Dict, Union, Optional

import altair as alt
import scipy.stats as sps
from KDEpy import FFTKDE

from salk_toolkit.utils import *
from salk_toolkit.io import extract_column_meta, read_json

# %% ../nbs/03_plots.ipynb 5
registry = {}
registry_meta = {}

# %% ../nbs/03_plots.ipynb 7
stk_plot_defaults = { 'data_format': 'longform' }

# Decorator for registering a plot type with metadata
def stk_plot(plot_name, **r_kwargs):
    
    def decorator(gfunc):
        # In theory, we could do transformations in wrapper
        # In practice, it would only obfuscate already complicated code
        #def wrapper(*args,**kwargs) :
        #    return gfunc(*args,**kwargs)

        # Register the function
        registry[plot_name] = gfunc
        registry_meta[plot_name] = { **stk_plot_defaults, **r_kwargs }
        
        return gfunc
    
    return decorator

def stk_deregister(plot_name):
    del registry[plot_name]
    del registry_meta[plot_name]

def get_plot_fn(plot_name):
    return registry[plot_name]

def get_plot_meta(plot_name):
    return registry_meta[plot_name]

# %% ../nbs/03_plots.ipynb 8
# First is weight if not matching, second if match
# This is very much a placeholder right now
priority_weights = {
    'likert': [-10000, 100],
    'continuous': [0, 100],
    'draws': [0,50],
    'question': [0, 100],
    'ordered': [-10000,100],
    'ordered_factor':[-10000,100],
    'requires_factor':[-10000,0],
    'factor_meta':[-10000,0]
}

def calculate_priority(plot_meta, match):
    base = 0
    if 'factor_meta' in plot_meta: # Somewhat hacky way of adding this but it works
        if len(set(plot_meta['factor_meta']) - set(match['factor_meta']))>0: base += priority_weights['factor_meta'][0]
                                                                 
    return base + sum([ priority_weights[k][vod(match,k) or 0] for k, v in plot_meta.items() if k not in ['factor_meta'] and k in priority_weights and v ])

def calculate_impossibilities(plot_meta, match):
    return [ k for k, v in plot_meta.items() if k not in ['factor_meta'] and k in priority_weights and v and priority_weights[k][vod(match,k) or 0]<0 ]

# Get a list of plot types matching required spec
def matching_plots(args, df, data_meta, details=False):
    
    rc = args['res_col']
    col_meta = extract_column_meta(data_meta)
    
    match = {
        'draws': ('draw' in df.columns),
        'likert': vod(col_meta[rc],'likert'),
        'question': (rc not in df.columns),
        'continuous': ('categories' not in col_meta[rc]),
        'ordered': vod(col_meta[rc],'ordered'),
        'ordered_factor': (vod(args,'factor_cols',[])==[]) or not vod(args,'internal_facet') or vod(col_meta[args['factor_cols'][0]],'ordered'),
        'requires_factor': (vod(args,'factor_cols',[])!=[]) and vod(args,'internal_facet'),
    }
    
    if vod(args,'convert_res')=='continuous' and vod(col_meta[rc],'ordered'):
        match = {**match,'continuous':True,'ordered':False,'likert':False}
    
    match['factor_meta'] = col_meta[args['factor_cols'][0]] if match['requires_factor'] else []
    
    res = [ ( pn, calculate_priority(get_plot_meta(pn),match), calculate_impossibilities(get_plot_meta(pn),match)) for pn in registry.keys() ]
    
    if details: return { n: (p, i) for (n, p, i) in res } # Return dict with priorities and failure reasons
    else: return [ n for (n,p,i) in sorted(res,key=lambda t: t[1], reverse=True) if p >= 0 ] # Return list of possibilities in decreasing order of fit

# %% ../nbs/03_plots.ipynb 13
# Create and register a continuous version of the cat_fn_name plot with 'question' dimension replacing category
# This assumes the plot can display not just percentages but all real numbers
def register_stk_cont_version(cat_fn_name):
    cat_fn, cat_fn_meta = get_plot_fn(cat_fn_name), get_plot_meta(cat_fn_name)
    @stk_plot(f'{cat_fn_name}-cont', **{**cat_fn_meta, **{'continuous':True, 'question':True} })
    def cont(data, value_col='value', question_color_scale=alt.Undefined, question_order=alt.Undefined, **kwargs):
        
        # Remap certain args while keeping everything else intact
        kwargs = {**kwargs, **{'data':data, 'cat_col':'question', 'cat_order': question_order, 'value_col':value_col, 'color_scale':question_color_scale}}
        
        # Trim down parameters list if needed
        aspec = inspect.getfullargspec(cat_fn)
        if aspec.varkw is None: kwargs = { k:v for k,v in kwargs.items() if k in aspec.args }
        
        return cat_fn(**kwargs)
    return cont

# %% ../nbs/03_plots.ipynb 16
@stk_plot('boxplots', data_format='longform', draws=True)
def boxplots(data, cat_col, value_col='value', color_scale=alt.Undefined, cat_order=alt.Undefined, factor_col=None, factor_color_scale=alt.Undefined, factor_order=alt.Undefined, val_format='%'):
    if val_format[-1] == '%': # Boxplots being a compound plot, this workaround is needed for axis & tooltips to be proper
        data[value_col]*=100
        val_format = val_format[:-1]+'f'
    
    shared = {
        'y': alt.Y(f'{cat_col}:N', title=None, sort=cat_order),

        **({
            'color': alt.Color(f'{cat_col}:N', scale=color_scale, legend=None)    
            } if not factor_col else {
                'yOffset':alt.YOffset(f'{factor_col}:N', title=None, sort=factor_order), 
                'color': alt.Color(f'{factor_col}:N', scale=factor_color_scale, legend=alt.Legend(orient='top'))
            })
    }
    
    base = alt.Chart(round(data, 2))
    
    # This plot is here because boxplot does not draw if variance is very low, so this is the backup
    tick_plot = base.mark_tick(thickness=3).encode(
        x=alt.X(f'mean({value_col}):Q'),
        tooltip=[
            alt.Tooltip(f'mean({value_col}):Q'),
            *([alt.Tooltip(f'{factor_col}:N')] if factor_col else []),
            alt.Tooltip(f'{cat_col}:N')
        ],
        **shared
    )
    
    box_plot = base.mark_boxplot(
        clip=True,
        #extent='min-max',
        outliers=False
    ).encode(
        x=alt.X(
            f'{value_col}:Q',
            title=value_col,
            axis=alt.Axis(format=val_format)
            ),
        tooltip=[
            *([alt.Tooltip(f'{factor_col}:N')] if factor_col else []),
            alt.Tooltip(f'{cat_col}:N'),
            #alt.Tooltip(f'median({value_col}:Q)',format=val_format)
        ],
        **shared,
    )
    return tick_plot + box_plot

register_stk_cont_version('boxplots')

# %% ../nbs/03_plots.ipynb 18
@stk_plot('columns', data_format='longform', draws=False)
def columns(data, cat_col, value_col='value', color_scale=alt.Undefined, cat_order=alt.Undefined, factor_col=None, factor_color_scale=alt.Undefined, factor_order=alt.Undefined, val_format='%'):
    plot = alt.Chart(round(data, 3), width = 'container' \
    ).mark_bar().encode(
        y=alt.Y(f'{cat_col}:N', title=None, sort=cat_order),
        x=alt.X(
            f'{value_col}:Q',
            title=value_col,
            axis=alt.Axis(format=val_format),
            #scale=alt.Scale(domain=[0,30]) #see lõikab mõnedes jaotustes parema ääre ära
            ),
        tooltip = [
            *([alt.Tooltip(f'{factor_col}:N')] if factor_col else []),
            alt.Tooltip(f'{cat_col}:N'),
            alt.Tooltip(f'{value_col}:Q',format=val_format)
        ],
        
        #tooltip=[
        #    'response:N',
            #alt.Tooltip('mean(support):Q',format='.1%')
        #    ],
        **({
                'color': alt.Color(f'{cat_col}:N', scale=color_scale, legend=None)    
            } if not factor_col else {
                'yOffset':alt.YOffset(f'{factor_col}:N', title=None, sort=factor_order), 
                'color': alt.Color(f'{factor_col}:N', scale=factor_color_scale, legend=alt.Legend(orient='top'))
            }),
    )
    return plot

register_stk_cont_version('columns')

# %% ../nbs/03_plots.ipynb 20
@stk_plot('diff_columns', data_format='longform', draws=False, requires_factor=True, args={'sort_descending':'bool'})
def diff_columns(data, cat_col, value_col='value', color_scale=alt.Undefined, cat_order=alt.Undefined, factor_col=None, factor_color_scale=alt.Undefined, val_format='%', sort_descending=False):
    
    ind_cols = list(set(data.columns)-{value_col,factor_col})
    factors = data[factor_col].unique() # use unique instead of categories to allow filters to select the two that remain
    
    idf = data.set_index(ind_cols)
    diff = (idf[idf[factor_col]==factors[1]][value_col]-idf[idf[factor_col]==factors[0]][value_col]).reset_index()
    
    if sort_descending: cat_order = list(diff.sort_values(value_col,ascending=False)[cat_col])
    
    plot = alt.Chart(round(diff, 3), width = 'container' \
    ).mark_bar().encode(
        y=alt.Y(f'{cat_col}:N', title=None, sort=cat_order),
        x=alt.X(
            f'{value_col}:Q',
            title=f"{factors[1]} - {factors[0]}",
            axis=alt.Axis(format=val_format, title=f"{factors[0]} <> {factors[1]}"),
            #scale=alt.Scale(domain=[0,30]) #see lõikab mõnedes jaotustes parema ääre ära
            ),
        
        tooltip=[
            alt.Tooltip(f'{cat_col}:N'),
            alt.Tooltip(f'{value_col}:Q',format=val_format, title=f'{value_col} difference')
            ],
        color=alt.Color(f'{cat_col}:N', scale=color_scale, legend=None)    
    )
    return plot

register_stk_cont_version('diff_columns')

# %% ../nbs/03_plots.ipynb 22
# Make the likert bar pieces
def make_start_end(x,value_col):
    #print("######################")
    #print(x)
    if len(x)!=5: return 
    scale_start=1
    x_mid = x.iloc[2:3,:]
    x_mid.loc[:,'end'] = -scale_start+x_mid[value_col]
    x_mid.loc[:,'start'] = -scale_start
    x_other = x.iloc[[0,1,3,4],:]
    x_other.loc[:,'end'] = x_other[value_col].cumsum() - x_other[0:2][value_col].sum()
    x_other.loc[:,'start'] = (x_other[value_col][::-1].cumsum()[::-1] - x_other[2:4][value_col].sum())*-1
    return pd.concat([x_other, x_mid])

@stk_plot('likert_bars',data_format='longform',question=True,draws=False,likert=True)
def likert_bars(data, cat_col, value_col='value', question_order=alt.Undefined, color_scale=alt.Undefined, factor_col=None, factor_color_scale=alt.Undefined, factor_order=alt.Undefined):
    gb_cols = list(set(data.columns)-{ cat_col, value_col }) # Assume all other cols still in data will be used for factoring
    
    options_cols = list(data[cat_col].dtype.categories) # Get likert scale names
    bar_data = data.groupby(gb_cols, group_keys=False).apply(make_start_end,value_col=value_col)
    
    plot = alt.Chart(bar_data).mark_bar() \
        .encode(
            x=alt.X('start:Q', axis=alt.Axis(title=None, format = '%')),
            x2=alt.X2('end:Q'),
            y=alt.Y(f'question:N', axis=alt.Axis(title=None, offset=5, ticks=False, minExtent=60, domain=False), sort=question_order),
            tooltip=[*([alt.Tooltip(f'{factor_col}:N')] if factor_col else []),
                    alt.Tooltip('question:N'), alt.Tooltip(f'{cat_col}:N'), alt.Tooltip(f'{value_col}:Q', title=value_col, format='.1%')],
            color=alt.Color(
                f'{cat_col}:N',
                legend=alt.Legend(
                    title='Response',
                    orient='bottom',
                    ),
                scale=alt.Scale(domain=options_cols, range=["#c30d24", "#f3a583", "#cccccc", "#94c6da", "#1770ab"]),
            ),
            **({ 'yOffset':alt.YOffset(f'{factor_col}:N', title=None, sort=factor_order),
                 #'stroke': alt.Stroke(f'{factor_col}:N', scale=factor_color_scale, legend=alt.Legend(orient='top')),
                 #'strokeWidth': alt.value(3)
               } if factor_col else {})
        )
    return plot

# %% ../nbs/03_plots.ipynb 24
# Calculate KDE ourselves using a fast libary. This gets around having to do sampling which is unstable
def kde_1d(vc, value_col):
    ls = np.linspace(vc.min()-1e-10,vc.max()+1e-10,200)
    y =  FFTKDE(kernel='gaussian').fit(vc.to_numpy()).evaluate(ls)
    return pd.DataFrame({'density': y, value_col: ls})

@stk_plot('density', data_format='raw', continuous=True, factor_columns=3,aspect_ratio=(1.0/1.0))
def density(data, value_col='value',factor_col=None, factor_color_scale=alt.Undefined):
    gb_cols = list(set(data.columns)-{ value_col }) # Assume we groupby over everything except value
    ndata = data.groupby(gb_cols)[value_col].apply(kde_1d,value_col=value_col).reset_index()
    
    plot = alt.Chart(
            ndata
        ).mark_line().encode(
            x=alt.X(f"{value_col}:Q"),
            y=alt.Y('density:Q',axis=alt.Axis(title=None, format = '%')),
            **({'color': alt.Color(f'{factor_col}:N', scale=factor_color_scale, legend=alt.Legend(orient='top'))} if factor_col else {})
        )
    return plot

# %% ../nbs/03_plots.ipynb 26
@stk_plot('matrix', data_format='longform', requires_factor=True, aspect_ratio=(1/0.8))
def matrix(data, cat_col, value_col='value', cat_order=alt.Undefined, factor_col=None, factor_color_scale=alt.Undefined, factor_order=alt.Undefined, val_format='%'):
    base = alt.Chart(data).mark_rect().encode(
            x=alt.X(f'{factor_col}:N', title=None, sort=factor_order),
            y=alt.Y(f'{cat_col}:N', title=None, sort=cat_order),
            color=alt.Color(f'{value_col}:Q', scale=alt.Scale(scheme='redyellowgreen', domainMid=0),
                legend=alt.Legend(title=None)),
            tooltip=[*([factor_col] if factor_col else []), alt.Tooltip(f'{cat_col}:N'), alt.Tooltip(f'{value_col}:Q', title=None, format=val_format)],
        )

    text = base.mark_text().encode(
        text=alt.Text(f'{value_col}:Q', format=val_format),
        color=alt.condition(
            alt.datum[f'{value_col}:Q']**2 > 1.5,
            alt.value('white'),
            alt.value('black')
        ),
        tooltip=[
            alt.Tooltip(f'{cat_col}:N'),
            *([alt.Tooltip(f'{factor_col}:N')] if factor_col else []),
            alt.Tooltip(f'{value_col}:Q', format=val_format)]
    )
    
    return base+text

register_stk_cont_version('matrix')

# %% ../nbs/03_plots.ipynb 30
@stk_plot('lines',data_format='longform', question=False, draws=False, ordered_factor=True, requires_factor=True, args={'smooth':'bool'})
def lines(data, cat_col, value_col='value', color_scale=alt.Undefined, cat_order=alt.Undefined, factor_col=None, factor_order=alt.Undefined, smooth=False):
    if smooth:
        smoothing = 'basis'
        points = 'transparent'
    else:
        smoothing = 'natural'
        points = True

    plot = alt.Chart(data).mark_line(point=points, interpolate=smoothing).encode(
        alt.X(f'{factor_col}:O', title=None, sort=factor_order),
        alt.Y(f'{value_col}:Q', title=None, axis=alt.Axis(format='%')),
        tooltip=[
            *([alt.Tooltip(f'{factor_col}:N')] if factor_col else []),
            alt.Tooltip(f'{value_col}:Q', format='.1%')],
        color=alt.Color(f'{cat_col}:N', scale=color_scale, sort=cat_order, legend=alt.Legend(orient='top'))
    )
    return plot


# %% ../nbs/03_plots.ipynb 32
@stk_plot('area_smooth',data_format='longform', question=False, draws=False, ordered=False, ordered_factor=True, requires_factor=True)
def area_smooth(data, cat_col, value_col='value', color_scale=alt.Undefined, cat_order=alt.Undefined, factor_col=None, factor_order=alt.Undefined,):
    ldict = dict(zip(cat_order, range(len(cat_order))))
    data.loc[:,'order'] = data[cat_col].replace(ldict)
    #print(data[[cat_col,'order']])
    plot=alt.Chart(data
        ).mark_area(interpolate='natural').encode(
            x=alt.X(f'{factor_col}:O', title=None, sort=factor_order),
            y=alt.Y(f'{value_col}:Q', title=None, stack='normalize',
                 scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format='%')
                 ),
            order=alt.Order("order:O"),
            color=alt.Color(f"{cat_col}:N", legend=alt.Legend(orient='top', title=None),
                sort=cat_order, scale=color_scale
                ),
            #tooltip=[alt.Tooltip(teema, title='vastus'), 'laine',
            #    alt.Tooltip('pct:Q', title='osakaal', format='.1%')]
        )
    return plot

# %% ../nbs/03_plots.ipynb 34
def likert_aggregate(x, cat_col, value_col):
    
    cc, vc = x[cat_col], x[value_col]
    cats = cc.dtype.categories
    
    #print(len(x),x.columns,x.head())
    pol = ( np.minimum(
                vc[cc.isin([cats[0], cats[1]])].sum(),
                vc[cc.isin([cats[3], cats[4]])].sum()
            ) / vc[cc !=  cats[2]].sum() )

    rad = ( vc[cc.isin([cats[0],cats[4]])].sum() /
            vc[cc != cats[2]].sum() )

    rel = vc[cc == cats[2]].sum()/vc.sum()

    return pd.Series({ 'polarisation': pol, 'radicalisation':rad, 'relevance':rel})

@stk_plot('likert_rad_pol',data_format='longform', question=False, draws=False, likert=True, requires_factor=True, args={'normalise':'bool'})
def likert_rad_pol(data, cat_col, value_col='value', factor_col=None, factor_color_scale=alt.Undefined, normalise=True):
    gb_cols = list(set(data.columns)-{ cat_col, value_col }) # Assume all other cols still in data will be used for factoring
    options_cols = list(data[cat_col].dtype.categories) # Get likert scale names
    likert_indices = data.groupby(gb_cols, group_keys=False).apply(likert_aggregate,cat_col=cat_col,value_col=value_col).reset_index()
    
    if normalise: likert_indices.loc[:,['polarisation','radicalisation']] = likert_indices[['polarisation','radicalisation']].apply(sps.zscore)
    
    plot = alt.Chart(likert_indices).mark_circle().encode(
        x=alt.X('polarisation:Q'),
        y=alt.Y('radicalisation:Q'),
        size=alt.Size('relevance:Q', legend=None, scale=alt.Scale(range=[100, 500])),
        opacity=alt.value(1.0),
        stroke=alt.value('#777'),
        tooltip=[
            *([alt.Tooltip(f'{factor_col}:N')] if factor_col else []),
            alt.Tooltip('radicalisation:Q', format='.2'),
            alt.Tooltip('polarisation:Q', format='.2'),
            alt.Tooltip('relevance:Q', format='.2')
        ],
        **({'color': alt.Color(f'{factor_col}:N', scale=factor_color_scale, legend=alt.Legend(orient='top'))} if factor_col else {})
        )
    return plot

# %% ../nbs/03_plots.ipynb 37
@stk_plot('geoplot', data_format='longform', continuous=True, requires_factor=True, factor_meta=['topo_feature'],aspect_ratio=(4.0/3.0))
def geoplot(data, topo_feature, value_col='value', color_scale=alt.Undefined, cat_order=alt.Undefined, factor_col=None, val_format='.2f'):
    
    tjson_url, tjson_meta, tjson_col = topo_feature
    source = alt.topo_feature(tjson_url, tjson_meta)

    plot = alt.Chart(source).mark_geoshape(stroke='white', strokeWidth=0.1).transform_lookup(
        lookup = f"properties.{tjson_col}",
        from_ = alt.LookupData(
            data=data,
            key=factor_col,
            fields=[value_col]
        ),
    ).encode(
        tooltip=[alt.Tooltip(f'properties.{tjson_col}:N', title=factor_col),
                alt.Tooltip(f'{value_col}:Q', title=value_col, format=val_format)],
        color=alt.Color(
            f'{value_col}:Q',
            scale=alt.Scale(scheme="reds"), # To use color scale, consider switching to opacity for value
            legend=alt.Legend(format=val_format, title=None, orient='right'),
        )
    ).project('mercator')
    return plot
