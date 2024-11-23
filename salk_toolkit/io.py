"""Functions to handle reading and writing datasets and model descriptions"""

# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/01_io.ipynb.

# %% auto 0
__all__ = ['max_cats', 'custom_meta_key', 'read_json', 'process_annotated_data', 'read_annotated_data', 'extract_column_meta',
           'group_columns_dict', 'list_aliases', 'change_meta_df', 'change_parquet_meta', 'infer_meta',
           'data_with_inferred_meta', 'read_and_process_data', 'save_population_h5', 'load_population_h5',
           'save_sample_h5', 'find_type_in_dict', 'save_parquet_with_metadata', 'load_parquet_metadata',
           'load_parquet_with_metadata', 'replace_data_meta_in_parquet']

# %% ../nbs/01_io.ipynb 3
import json, os, warnings
import itertools as it
from collections import defaultdict

import numpy as np
import pandas as pd
import polars as pl
import datetime as dt

from typing import List, Tuple, Dict, Union, Optional

import pyarrow as pa
import pyarrow.parquet as pq
import pyreadstat

import salk_toolkit as stk
from salk_toolkit.utils import replace_constants, is_datetime, warn, cached_fn

# %% ../nbs/01_io.ipynb 4
def read_json(fname,replace_const=True):
    with open(fname,'r') as jf:
        meta = json.load(jf)
    if replace_const:
        meta = replace_constants(meta)
    return meta

# %% ../nbs/01_io.ipynb 5
# Read files listed in meta['file'] or meta['files']
def read_concatenate_files_list(meta,data_file=None,path=None):

    opts = meta['read_opts'] if'read_opts' in meta else {}
    if data_file: data_files = [{ 'file': data_file, 'opts': opts}]
    elif 'file' in meta: data_files = [{ 'file': meta['file'], 'opts': opts }]
    elif 'files' in meta: data_files = meta['files'] 
    else: raise Exception("No files provided")
    
    data_files = [  {'opts': opts, **f } if isinstance(f,dict) else
                    {'opts': opts, 'file': f } for f in data_files ]
    
    cat_dtypes = {}
    raw_dfs, metas = [], []
    for fi, fd in enumerate(data_files):
        
        data_file, opts = fd['file'], fd['opts']
        if path: data_file = os.path.join(os.path.dirname(path),data_file)
        
        if data_file[-4:] == 'json' or data_file[-7:] == 'parquet': # Allow loading metafiles or annotated data
            if data_file[-4:] == 'json': warn(f"Processing {data_file}") # Print this to separate warnings for input jsons from main 
            raw_data, meta = read_annotated_data(data_file, infer=False)
            if meta is not None: metas.append(meta)
        elif data_file[-3:] in ['csv', '.gz']:
            raw_data = pd.read_csv(data_file, low_memory=False, **opts)
        elif data_file[-3:] in ['sav','dta']:
            read_fn = getattr(pyreadstat,'read_'+data_file[-3:])
            with warnings.catch_warnings(): # While pyreadstat has not been updated to pandas 2.2 standards
                warnings.simplefilter("ignore")
                raw_data, _ = read_fn(data_file, **{ 'apply_value_formats':True, 'dates_as_pandas_datetime':True },**opts)
        elif data_file[-4:] in ['.xls', 'xlsx', 'xlsm', 'xlsb', '.odf', '.ods', '.odt']:
            raw_data = pd.read_excel(data_file, **opts)
        else:
            raise Exception(f"Not a known file format for {data_file}")
        
        # If data is multi-indexed, flatten the index
        if isinstance(raw_data.columns,pd.MultiIndex): raw_data.columns = [" | ".join(tpl) for tpl in raw_data.columns]
        
        # Add extra columns to raw data that contain info about the file. Always includes column 'file' with filename and file_ind with index
        # Can be used to add survey_date or other useful metainfo
        if len(data_files)>1: raw_data['file_ind'] = fi
        for k,v in fd.items():
            if k in ['opts']: continue
            if len(data_files)<=1 and k in ['file']: continue
            raw_data[k] = v
            if isinstance(v,str): cat_dtypes[k] = None

        # Strip all categorical dtypes
        if len(data_files) > 1: # No point if only one file
            for c in raw_data.columns:
                if raw_data[c].dtype.name == 'category':
                    if c not in cat_dtypes or len(cat_dtypes[c].categories)<=len(raw_data[c].dtype.categories):
                        cat_dtypes[c] = raw_data[c].dtype
                    raw_data[c] = raw_data[c].astype('object')
            
        raw_dfs.append(raw_data)

    fdf = pd.concat(raw_dfs)

    # Restore categoricals
    if len(cat_dtypes)>0:
        for c, dtype in cat_dtypes.items():
            if dtype is None: # Added as an extra field, infer categories
                dtype = pd.Categorical([],list(fdf[c].dropna().unique())).dtype
            elif not set(fdf[c].dropna().unique()) <= set(dtype.categories): # If the categories are the same, restore the dtype
                #print(set(fdf[c].dropna().unique()), set(dtype.categories))
                warn(f"Categories for {c} are different between files, not restoring dtype")
                dtype = pd.Categorical([],list(fdf[c].dropna().unique())).dtype
            fdf[c] = pd.Categorical(fdf[c],dtype=dtype)

    return fdf, (metas[-1] if metas else None)

# %% ../nbs/01_io.ipynb 6
# convert number series to categorical, avoiding long and unweildy fractions like 24.666666666667
# This is a practical judgement call right now - round to two digits after comma and remove .00 from integers
def convert_number_series_to_categorical(s):
    return s.astype('float').map('{:.2f}'.format).str.replace('.00','').replace({'nan':None})

# %% ../nbs/01_io.ipynb 7
# Default usage with mature metafile: process_annotated_data(<metafile name>)
# When figuring out the metafile, it can also be run as: process_annotated_data(meta=<dict>, data_file=<>)
def process_annotated_data(meta_fname=None, meta=None, data_file=None, raw_data=None, return_meta=False, only_fix_categories=False, return_raw=False, virtual_pass=False):
    # Read metafile
    if meta_fname is not None:
        meta = read_json(meta_fname,replace_const=False)
    
    # Setup constants with a simple replacement mechanic
    constants = meta['constants'] if 'constants' in meta else {}
    meta = replace_constants(meta)
    
    # Read datafile(s)
    if raw_data is None:
        raw_data, inp_meta = read_concatenate_files_list(meta,data_file,path=meta_fname)
        if inp_meta is not None: warn(f"Processing main meta file") # Print this to separate warnings for input jsons from main 

    if return_raw: return (raw_data, meta) if return_meta else raw_data
    
    globs = {'pd':pd, 'np':np, 'stk':stk, 'df':raw_data, **constants }
    
    pp_key = 'preprocessing' if not virtual_pass else 'virtual_preprocessing'
    if pp_key in meta and not only_fix_categories:
        exec(meta[pp_key],globs)
        raw_data = globs['df']
    
    ndf = pd.DataFrame() if not virtual_pass else raw_data # In vitrual pass, start with the raw_data as it is already processed by normal steps
    all_cns = dict()
    for group in meta['structure']:
        if group.get('virtual',False) != virtual_pass: continue
        if group['name'] in all_cns:
            raise Exception(f"Group name {group['name']} duplicates a column name in group {all_cns[cn]}") 
        all_cns[group['name']] = group['name']
        g_cols = []
        for tpl in group['columns']:
            if type(tpl)==list:
                cn = tpl[0] # column name
                sn = tpl[1] if len(tpl)>1 and type(tpl[1])==str else cn # source column
                cd = tpl[2] if len(tpl)==3 else tpl[1] if len(tpl)==2 and type(tpl[1])==dict else {} # metadata
            else:
                cn = sn = tpl
                cd = {}

            if 'scale' in group: cd = {**group['scale'],**cd}

            # Col prefix is used to avoid name clashes when different groups naturally share same column names
            if 'col_prefix' in cd: cn = cd['col_prefix']+cn
            
            # Detect duplicate columns in meta - including among those missing or generated
            # Only flag if they are duplicates even after prefix
            if cn in all_cns: 
                raise Exception(f"Duplicate column name found: '{cn}' in {all_cns[cn]} and {group['name']}")
            all_cns[cn] = group['name']
                
            if only_fix_categories: sn = cn
            g_cols.append(cn)
            
            if sn not in raw_data:
                if not group.get('generated') and not group.get('virtual'): # bypass warning for columns marked as being generated later
                    warn(f"Column {sn} not found")
                continue
            
            if raw_data[sn].isna().all():
                warn(f"Column {sn} is empty and thus ignored")
                continue
                
            s = raw_data[sn]
            
            if not only_fix_categories:
                if s.dtype.name=='category': s = s.astype('object') # This makes it easier to use common ops like replace and fillna
                if 'translate' in cd: 
                    s = s.astype('str').replace(cd['translate']).replace('nan',None).replace('None',None)
                if 'transform' in cd: s = eval(cd['transform'],{ 's':s, 'df':raw_data, 'ndf':ndf, 'pd':pd, 'np':np, 'stk':stk , **constants })
                if 'translate_after' in cd: 
                    s = pd.Series(s).astype('str').replace(cd['translate_after']).replace('nan',None).replace('None',None)
                
                if cd.get('datetime'): s = pd.to_datetime(s,errors='coerce')
                elif cd.get('continuous'): s = pd.to_numeric(s,errors='coerce')

            s = pd.Series(s,name=cn) # In case transformation removes the name or renames it

            if 'categories' in cd: 
                na_sum = s.isna().sum()
                
                if cd['categories'] == 'infer':
                    if s.dtype.name=='category': cd['categories'] = list(s.dtype.categories) # Categories come from data file
                    elif 'translate' in cd and 'transform' not in cd and set(cd['translate'].values()) >= set(s.dropna().unique()): # Infer order from translation dict
                        cd['categories'] = pd.unique(np.array(list(cd['translate'].values())).astype('str')).tolist()
                        s = s.astype('str')
                    else: # Just use lexicographic ordering
                        if cd.get('ordered',False) and not pd.api.types.is_numeric_dtype(s):
                            warn(f"Ordered category {cn} had category: infer. This only works correctly if you want lexicographic ordering!")
                        if not pd.api.types.is_numeric_dtype(s): s.loc[~s.isna()] = s[~s.isna()].astype(str) # convert all to string to avoid type issues in sorting for mixed columns
                        cinds = s.drop_duplicates().sort_values().index # NB! Important to do this still with numbers before converting them to strings
                        if pd.api.types.is_numeric_dtype(s): s = convert_number_series_to_categorical(s)
                        cd['categories'] = [ c for c in s[cinds] if pd.notna(c) ] # Also propagates it into meta (unless shared scale)

                    
                cats = cd['categories']
                s_rep = s.dropna().iloc[0] # Find a non-na element
                if isinstance(s_rep,list) or isinstance(s_rep,np.ndarray): 
                    print(cn,s_rep)
                    ns = s #  Just leave a list of strings
                else: ns = pd.Series(pd.Categorical(s, # NB! conversion to str already done before. Doing it here kills NA values
                                                    categories=cats,ordered=cd['ordered'] if 'ordered' in cd else False), name=cn, index=raw_data.index)
                # Check if the category list provided was comprehensive
                new_nas = ns.isna().sum() - na_sum
                
                if new_nas > 0: 
                    unlisted_cats = set(s.dropna().unique())-set(cats)
                    warn(f"Column {cn} {f'({sn}) ' if cn != sn else ''} had unknown categories {unlisted_cats} for { new_nas/len(ns) :.1%} entries")
                    
                s = ns
            
            # Update ndf in real-time so it would be usable in transforms for next columns
            if s.name in ndf.columns: ndf = ndf.drop(columns=s.name) # Overwrite existing instead of duplicates. Esp. important for virtual cols
            ndf = pd.concat([ndf,s],axis=1)

        if 'subgroup_transform' in group:
            subgroups = group.get('subgroups',[g_cols])
            for sg in subgroups:
                ndf[sg] = eval(group['subgroup_transform'],{ 'gdf':ndf[sg], 'df':raw_data, 'ndf':ndf, 'pd':pd, 'np':np, 'stk':stk , **constants })

    pp_key = 'postprocessing' if not virtual_pass else 'virtual_postprocessing'
    if pp_key in meta and not only_fix_categories:
        globs['df'] = ndf
        exec(meta[pp_key],globs)
        ndf = globs['df']
    
    return (ndf, meta) if return_meta else ndf

# %% ../nbs/01_io.ipynb 8
# Read either a json annotation and process the data, or a processed parquet with the annotation attached
# Return_raw is here for easier debugging of metafiles and is not meant to be used in production
def read_annotated_data(fname, infer=True, return_raw=False, return_model_meta=False):
    _, ext = os.path.splitext(fname)
    meta, model_meta = None, None
    if ext == '.json':
        data, meta =  process_annotated_data(fname, return_meta=True, return_raw=return_raw)
    elif ext == '.parquet':
        data, full_meta = load_parquet_with_metadata(fname)
        if full_meta is not None: 
            meta, model_meta = full_meta.get('data'), full_meta.get('model')
            if meta is not None and not return_raw: # Do the second, virtual pass
                data, meta = process_annotated_data(meta=meta, raw_data=data, virtual_pass=True, return_meta=True)
    
    mm = (model_meta,) if return_model_meta else tuple()
    if meta is not None or not infer:
        return (data, meta) + mm
    
    warn(f"Warning: using inferred meta for {fname}")
    meta = infer_meta(fname,meta_file=False)
    return process_annotated_data(fname, meta=meta, return_meta=True) + mm

# %% ../nbs/01_io.ipynb 9
# Helper functions designed to be used with the annotations

# Convert data_meta into a dict where each group and column maps to their metadata dict
def extract_column_meta(data_meta):
    res = defaultdict(lambda: {})
    for g in data_meta['structure']:
        base = g['scale'] if 'scale' in g else {}
        res[g['name']] = {**base, 'columns': [base.get('col_prefix','')+(t[0] if type(t)!=str else t) for t in g['columns']] }
        for cd in g['columns']:
            if isinstance(cd,str): cd = [cd]
            res[base.get('col_prefix','')+cd[0]] = {**base,**cd[-1]} if isinstance(cd[-1],dict) else base
    return res

# Convert data_meta into a dict of group_name -> [column names]
# TODO: deprecate - info available in extract_column_meta
def group_columns_dict(data_meta):
    return { k: d['columns'] for k,d in extract_column_meta(data_meta).items() if 'columns' in d }

    #return { g['name'] : [(t[0] if type(t)!=str else t) for t in g['columns']] for g in data_meta['structure'] }

# Take a list and a dict and replace all dict keys in list with their corresponding lists in-place
def list_aliases(lst, da):
    return [ fv for v in lst for fv in (da[v] if isinstance(v,str) and v in da else [v]) ]

# %% ../nbs/01_io.ipynb 11
# Creates a mapping old -> new
def get_original_column_names(dmeta):
    res = {}
    for g in dmeta['structure']:
        for c in g['columns']:
            if isinstance(c,str): res[c] = c
            if len(c)==1: res[c[0]] = c[0]
            elif len(c)>=2 and isinstance(c[1],str): res[c[1]] = c[0]
    return res

# Map ot backwards and nt forwards to move from one to the other
def change_mapping(ot, nt, only_matches=False):
    # Todo: warn about non-bijective mappings
    matches = { v: nt[k] for k, v in ot.items() if k in nt and v!=nt[k] } # change those that are shared
    if only_matches: return matches
    else: 
        return { **{ v:k for k, v in ot.items() if k not in nt }, # undo those in ot not in nt
                 **{ k:v for k, v in nt.items() if k not in ot }, # do those in nt not in ot
                 **matches } 

# %% ../nbs/01_io.ipynb 12
# Change an existing dataset to correspond better to a new meta_data
# This is intended to allow making small improvements in the meta even after a model has been run
# It is by no means perfect, but is nevertheless a useful tool to avoid re-running long pymc models for simple column/translation changes
def change_meta_df(df, old_dmeta, new_dmeta):
    warn("This tool handles only simple cases of column name, translation and category order changes.")
    
    # Ready the metafiles for parsing
    old_dmeta = replace_constants(old_dmeta); new_dmeta = replace_constants(new_dmeta)
    
    # Rename columns 
    ocn, ncn = get_original_column_names(old_dmeta), get_original_column_names(new_dmeta)
    name_changes = change_mapping(ocn,ncn,only_matches=True)
    if name_changes != {}: print(f"Renaming columns: {name_changes}")
    df.rename(columns=name_changes,inplace=True)
    
    rev_name_changes = { v: k for k,v in name_changes.items() }
    
    # Get metadata for each column
    ocm = extract_column_meta(old_dmeta)
    ncm = extract_column_meta(new_dmeta)
    
    for c in ncm.keys():
        if c not in df.columns: continue # probably group
        if c not in ocm.keys(): continue # new column
        
        ncd, ocd = ncm[c], ocm[rev_name_changes[c] if c in rev_name_changes else c]
        
        # Warn about transformations and don't touch columns where those change
        if ocd.get('transform') != ncd.get('transform'):
            warn(f"Column {c} has a different transformation. Leaving it unchanged")
            continue
        
        # Handle translation changes
        ot, nt = ocd.get('translate',{}), ncd.get('translate',{})
        remap = change_mapping(ot,nt)
        if remap != {}: print(f"Remapping {c} with {remap}")
        df[c].replace(remap,inplace=True)
        
        # Reorder categories and/or change ordered status
        if ocd.get('categories') != ncd.get('categories') or ocd.get('ordered') != ncd.get('ordered'):
            cats = ncd.get('categories')
            if isinstance(cats,list):
                print(f"Changing {c} to Cat({cats},ordered={ncd.get('ordered')}")
                df[c] = pd.Categorical(df[c],categories=cats,ordered=ncd.get('ordered'))
    
    # column order changes
    gcdict = group_columns_dict(new_dmeta)
    
    cols = ['draw','obs_idx'] + [ c for g in new_dmeta['structure'] for c in gcdict[g['name']]]
    cols = [ c for c in cols if c in df.columns ]
    
    return df[cols]

def change_parquet_meta(orig_file,data_metafile,new_file):
    df, meta = load_parquet_with_metadata(orig_file)
    
    new_data_meta = read_json(data_metafile, replace_const=True)
    df = change_meta_df(df,meta['data'],new_data_meta)
    
    meta['old_data'] = meta['data']
    meta['data'] = new_data_meta
    save_parquet_with_metadata(df,meta,new_file)
    
    return df, meta


# %% ../nbs/01_io.ipynb 13
def is_categorical(col):
    return col.dtype.name in ['object', 'str', 'category'] and not is_datetime(col)


# %% ../nbs/01_io.ipynb 14
max_cats = 50

# Create a very basic metafile for a dataset based on it's contents
# This is not meant to be directly used, rather to speed up the annotation process
def infer_meta(data_file=None, meta_file=True, read_opts={}, df=None, translate_fn=None, translation_blacklist=[]):
    meta = { 'constants': {}, 'read_opts': read_opts }

    if translate_fn is not None: 
        otfn = translate_fn
        translate_fn = cached_fn(lambda x: otfn(str(x)) if x else '' )
    else: translate_fn = str
    
    # Read datafile
    col_labels = {}
    if data_file is not None:
        path, fname = os.path.split(data_file)
        meta['file'] = fname
        if data_file[-3:] in ['csv', '.gz']:
            df = pd.read_csv(data_file, low_memory=False, **read_opts)
        elif data_file[-3:] in ['sav','dta']:
            read_fn = getattr(pyreadstat,'read_'+data_file[-3:])
            df, sav_meta = read_fn(data_file, **{ 'apply_value_formats':True, 'dates_as_pandas_datetime':True },**read_opts)
            col_labels = dict(zip(sav_meta.column_names, sav_meta.column_labels)) # Make this data easy to access by putting it in meta as constant
            if translate_fn: col_labels = { k: translate_fn(v) for k,v in col_labels.items() }
        elif data_file[-7:] == 'parquet':
            df = pd.read_parquet(data_file, **read_opts)
        elif data_file[-4:] in ['.xls', 'xlsx', 'xlsm', 'xlsb', '.odf', '.ods', '.odt']:
            df = pd.read_excel(data_file, **read_opts)
        else:
            raise Exception(f"Not a known file format {data_file}")
            
    # If data is multi-indexed, flatten the index
    if isinstance(df.columns,pd.MultiIndex): df.columns = [" | ".join(tpl) for tpl in df.columns]

    cats, grps = {}, defaultdict(lambda: list())
    
    main_grp = { 'name': 'main', 'columns':[] }
    meta['structure'] = [main_grp]
    
    # Remove empty columns
    cols = [ c for c in df.columns if df[c].notna().any() ]
    
    # Determine category lists for all categories
    for cn in cols:
        if not is_categorical(df[cn]): continue
        cats[cn] = sorted(list(df[cn].dropna().unique())) if df[cn].dtype.name != 'category' else list(df[cn].dtype.categories)
        
        for cs in grps:
            #if cn.startswith('Q2_'): print(len(set(cats[cn]) & cs)/len(cs),set(cats[cn]),cs)
            if len(set(cats[cn]) & cs)/len(cs) > 0.75: # match to group if most of the values match
                lst = grps[cs]
                del grps[cs]
                grps[frozenset(cs | set(cats[cn]))] = lst + [cn]
                break
        else:
            grps[frozenset(cats[cn])].append(cn)
        
    # Fn to create the meta for a categorical column
    def cat_meta(cn):
        m = { 'categories': cats[cn] if len(cats[cn])<=max_cats else 'infer' }
        if cn in df.columns and df[cn].dtype.name=='category' and df[cn].dtype.ordered: m['ordered'] = True
        if translate_fn is not None and cn not in translation_blacklist and len(cats[cn])<=max_cats:
            tdict = { c: translate_fn(c) for c in m['categories'] }
            m['categories'] = 'infer' #[ tdict[c] for c in m['categories'] ]
            m['translate'] = tdict
        return m
        
    
    # Create groups from values that share a category
    handled_cols = set()
    for k,g_cols in grps.items():
        if len(g_cols)<2: continue
        
        # Set up the columns part
        m_cols = []
        for cn in g_cols:
            ce = [cn,{'label': col_labels[cn]}] if cn in col_labels else [cn]
            if translate_fn is not None: ce = [translate_fn(cn)]+ ce
            if len(ce) == 1: ce = ce[0]
            m_cols.append(ce)
        
        kl = [ str(c) for c in k]
        cats[str(kl)] = kl # so cat_meta would use the full list

        grp = { 'name': ';'.join(kl), 'scale': cat_meta(str(kl)), 'columns': m_cols }
        
        meta['structure'].append(grp)
        handled_cols.update(g_cols)
        
    # Put the rest of variables into main category
    main_cols = [ c for c in cols if c not in handled_cols ]
    for cn in main_cols:
        if cn in cats: cdesc = cat_meta(cn)
        else: 
            if is_datetime(df[cn]): cdesc = {'datetime':True}
            else: cdesc = {'continuous':True}
        if cn in col_labels: cdesc['label'] = col_labels[cn]
        main_grp['columns'].append([cn,cdesc] if translate_fn is None else [translate_fn(cn),cn,cdesc])
        
    #print(json.dumps(meta,indent=2,ensure_ascii=False))
    
    # Write file to disk
    if data_file is not None and meta_file:
        if meta_file is True: meta_file = os.path.join(path, os.path.splitext(fname)[0]+'_meta.json')
        if not os.path.exists(meta_file):
            print(f"Writing {meta_file} to disk")
            with open(meta_file,'w',encoding='utf8') as jf:
                json.dump(meta,jf,indent=2,ensure_ascii=False)
        else:
            print(f"{meta_file} already exists, skipping write")

    return meta

# Small convenience function to have a meta available for any dataset
def data_with_inferred_meta(data_file, **kwargs):
    meta = infer_meta(data_file,meta_file=False, **kwargs)
    return process_annotated_data(meta=meta, data_file=data_file, return_meta=True)


# %% ../nbs/01_io.ipynb 16
def read_and_process_data(desc, return_meta=False, constants={}, skip_postprocessing=False):

    df, meta = read_concatenate_files_list(desc)

    if meta is None and return_meta:
        raise Exception("No meta found on any of the files")
    
    # Perform transformation and filtering
    globs = {'pd':pd, 'np':np, 'stk':stk, 'df':df, **constants}
    if desc.get('preprocessing'): exec(desc['preprocessing'], globs)
    if desc.get('filter'): globs['df'] = globs['df'][eval(desc['filter'], globs)]
    if desc.get('postprocessing') and not skip_postprocessing: exec(desc['postprocessing'],globs)
    df = globs['df']
    
    return (df, meta) if return_meta else df

# %% ../nbs/01_io.ipynb 18
def save_population_h5(fname,pdf):
    hdf = pd.HDFStore(fname,complevel=9, complib='zlib')
    hdf.put('population',pdf,format='table')
    hdf.close()
    
def load_population_h5(fname):
    hdf =  pd.HDFStore(fname, mode='r')
    res = hdf['population'].copy()
    hdf.close()
    return res

# %% ../nbs/01_io.ipynb 19
def save_sample_h5(fname,trace,COORDS = None, filter_df = None):
    odims = [d for d in trace.predictions.dims if d not in ['chain','draw','obs_idx']]
    
    if COORDS is None: # Recover them from trace (requires posterior be saved in same trace)
        inds = trace.posterior.indexes
        coords = { t: list(inds[t]) for t in inds if t not in ['chain','draw'] and '_dim_' not in t}
        COORDS = { 'immutable': coords, 'mutable': ['obs_idx'] }

    if filter_df is None: # Recover filter dimensions and data from trace (works only for GLMs)
        rmdims = odims + list({'time','unit','combined_inputs'} & set(trace.predictions_constant_data.dims))
        df = trace.predictions_constant_data.drop_dims(rmdims).to_dataframe()#.set_index(demographics_order).indexb
        df.columns = [ s.removesuffix('_id') for s in df.columns]
        df.drop(columns=[c for c in df.columns if c[:4]=='obs_'],inplace=True)

        for d in df.columns:
            if d in COORDS['immutable']:
                fs = COORDS['immutable'][d]
                df[d] = pd.Categorical(df[d].replace(dict(enumerate(fs))),fs)
                if d in orders: df[d] = pd.Categorical(df[d],orders[d],ordered=True)
        filter_df = df

    chains, draws = trace.predictions.dims['chain'], trace.predictions.dims['draw']
    dinds = np.array(list(it.product( range(chains), range(draws), list(filter_df.index)))).reshape( (-1, 3) )

    res_dfs = { 'filter': filter_df }
    for odim in odims:
        response_cols = list(np.array(trace.predictions[odim]))
        xdf = pd.DataFrame(np.concatenate( (
            dinds,
            np.array(trace.predictions['y_'+odim]).reshape( ( -1,len(response_cols) ) )
            ), axis=-1), columns = ['chain', 'draw', 'obs_idx'] + response_cols)
        res_dfs[odim] = postprocess_rdf(xdf,odim)
        
    # Save dfs as hdf5
    hdf = pd.HDFStore(fname,complevel=9, complib='zlib')
    for k,vdf in res_dfs.items():
        hdf.put(k,vdf,format='table')
    hdf.close()


# %% ../nbs/01_io.ipynb 20
# Small debug tool to help find where jsons become non-serializable
def find_type_in_dict(d,dtype,path=''):
    print(d,path)
    if isinstance(d,dict):
        for k,v in d.items():
            find_type_in_dict(v,dtype,path+f'{k}:')
    if isinstance(d,list):
        for i,v in enumerate(d):
            find_type_in_dict(v,dtype,path+f'[{i}]')
    elif isinstance(d,dtype):
        print("RES")
        raise Exception(f"Value {d} of type {dtype} found at {path}")

# %% ../nbs/01_io.ipynb 21
# These two very helpful functions are borrowed from https://towardsdatascience.com/saving-metadata-with-dataframes-71f51f558d8e

custom_meta_key = 'salk-toolkit-meta'

def save_parquet_with_metadata(df, meta, file_name):
    table = pa.Table.from_pandas(df)

    #find_type_in_dict(meta,np.int64)
    
    custom_meta_json = json.dumps(meta)
    existing_meta = table.schema.metadata
    combined_meta = {
        custom_meta_key.encode() : custom_meta_json.encode(),
        **existing_meta
    }
    table = table.replace_schema_metadata(combined_meta)
    
    pq.write_table(table, file_name, compression='GZIP')
    
# Just load the metadata from the parquet file
def load_parquet_metadata(file_name):
    schema = pq.read_schema(file_name)
    if custom_meta_key.encode() in schema.metadata:
        restored_meta_json = schema.metadata[custom_meta_key.encode()]
        restored_meta = json.loads(restored_meta_json)
    else: restored_meta = None
    return restored_meta
    
# Load parquet with metadata
def load_parquet_with_metadata(file_name,lazy=False,**kwargs):
    if lazy: # Load it as a polars lazy dataframe
        meta = load_parquet_metadata(file_name)
        pl.scan_parquet(file_name,**kwargs)
        return ldf, meta
    
    # Read it as a normal pandas dataframe
    restored_table = pq.read_table(file_name,**kwargs)
    restored_df = restored_table.to_pandas()
    if custom_meta_key.encode() in restored_table.schema.metadata:
        restored_meta_json = restored_table.schema.metadata[custom_meta_key.encode()]
        restored_meta = json.loads(restored_meta_json)
    else: restored_meta = None

    return restored_df, restored_meta



# %% ../nbs/01_io.ipynb 23
# Helper function to replace a data meta in an already sampled model
# Should be used carefully, but should mostly work. 

def replace_data_meta_in_parquet(parquet_name,metafile_name):
    df, fmeta = load_parquet_with_metadata(parquet_name)

    with open(metafile_name,'r') as jf:
        nmeta = json.load(jf)

    # Add the groups added by the model before to data_meta
    existing_grps = { g['name'] for g in nmeta['structure'] }
    nmeta['structure'] += [ grp for grp in fmeta['data']['structure']
        if grp.get('generated') and grp['name'] not in existing_grps ]

    # Replace the data part of meta    
    fmeta['data'] = nmeta

    # Rewrite the file
    save_parquet_with_metadata(df,fmeta,parquet_name)
