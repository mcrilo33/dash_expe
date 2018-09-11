# coding=utf-8

import os
import tempfile
from datetime import datetime, timedelta

import gridfs
import numpy as np
import pandas as pd
import plotly.graph_objs as go
import pymongo
import pymongo.database
import pymongo.mongo_client
from IPython.display import HTML
from bson.objectid import ObjectId
from pandas.io.json import json_normalize

# Pretty Pandas Dataframes
PANDAS_STYLE = HTML("""<style>
    .dataframe * {border-color: #c0c0c0 !important;}
    .dataframe th{background: #e8e8e8;}
    .dataframe thead th {text-align: center;}
    .dataframe td{
        background: #fff;
        text-align: right;
        min-width:5em;
    }
    .dataframe tr:nth-child(even) td{background: #f0f0f0;}
    .dataframe tr:nth-child(even) th{background: #e0e0e0;}
    .dataframe thead th:first-child{
        background: #fff;
        border-top-color: #fff !important;
        border-left-color: #fff !important;
        border-bottom-color: #fff !important;
    }
    .dataframe thead tr:last-child th:first-child{
        border-bottom-color: #c0c0c0 !important;
    }
</style>""")


def get_completed_query():
    return {'status': 'COMPLETED'}


def get_failed_query():
    return {'status': 'FAILED'}


def get_queued_query():
    return {'status': 'QUEUED'}


def get_interrupted_query():
    return {'status': 'INTERRUPTED'}


def get_timed_out_query():
    return {'status': 'TIMEOUT'}


def get_running_query():
    now = datetime.now()
    patience = timedelta(seconds=120)
    return {'status': 'RUNNING', 'heartbeat': {'$gt': now - patience}}


def get_died_query():
    now = datetime.now()
    patience = timedelta(seconds=120)
    return {'status': 'RUNNING', 'heartbeat': {'$lt': now - patience}}


def cat_to_boxplot(df, cat):
        dico = {}
        nb_cat = len(df[cat].cat.categories)
        dico['range']=[0,nb_cat-1] 
        dico['tickvals'] = list(range(nb_cat))
        dico['ticktext'] = df[cat].cat.categories
        dico['label'] = cat
        dico['values'] = df[cat].cat.codes
        l_trace = [] 
        for tick in dico['ticktext']:
                
            y = df[df[cat] == tick].result
            trace = go.Box(
                y=y,
                name = str(cat)+"_"+str(tick),
                boxmean = True,
            )
            l_trace.append(trace)
        return l_trace


def float_to_scatter(df, cat):
        trace_scatter_back = go.Scatter(
            y=df.result,
            x=df[cat],
            mode='markers',
            name=cat,
            text='id: ' + df._id.astype('str'),
        )
        layout= go.Layout(
        title= str(cat) + ' / result',
        hovermode= 'closest',
        xaxis= dict(
            title= cat,
            ticklen= 5,
            zeroline= False,
            gridwidth= 2,
            autorange=True
        ),
        yaxis=dict(
            title='Result',
            ticklen=5,
            gridwidth=2,
            autorange=True,
        ),
        showlegend=False
        )
        figure_backloss = go.Figure(data=[trace_scatter_back],layout=layout)
        return figure_backloss



stats_query_getters = {
    'TOTAL': lambda: {},
    'QUEUED': get_queued_query,
    'INTERRUPTED': get_interrupted_query,
    'TIMEOUT': get_timed_out_query,
    'RUNNING': get_running_query,
    'DIED': get_died_query,
    'FAILED': get_failed_query,
    'COMPLETED': get_completed_query
}


def sacred_stats(obj=None, filter_by=None, clean=True):
    obj = pymongo.MongoClient() if obj is None else obj
    filter_by = {} if filter_by is None else filter_by
    all_results = {}
    if isinstance(obj, pymongo.mongo_client.MongoClient):
        for db_name in obj.database_names():
            db_stats = get_db_stats(obj[db_name], filter_by)
            all_results.update({db_name + '.' + k: v
                                for k, v in db_stats.items()})
    elif isinstance(obj, pymongo.database.Database):
        all_results = get_db_stats(obj, filter_by)
    df = pd.DataFrame.from_dict(all_results, orient='index')
    if clean:
        df = df[df.columns[(df != 0).any()]]
    return df


def get_db_stats(db, filter_by):
    assert isinstance(filter_by, dict)

    def count(coll, q):
        query = q()
        query.update(filter_by)
        return coll.find(query).count()

    collection_stats = {}
    for cname in db.collection_names():
        if cname in {'system.indexes', '_properties', 'fs.files', 'fs.chunks'}:
            continue
        coll = db[cname]
        stats = {n: count(coll, q) for n, q in stats_query_getters.items()}
        collection_stats[cname] = stats
    return collection_stats


def get_by_dotted_path(d, path):
    """
    Get an entry from nested dictionaries using a dotted path.

    Example:
    >>> get_by_dotted_path({'foo': {'a': 12}}, 'foo.a')
    12
    """
    if not path:
        return d
    split_path = path.split('.')
    current_option = d
    for p in split_path:
        if p not in current_option:
            return None
        current_option = current_option[p]
    return current_option


def convert_json_to_nice_dataframe(json_doc, prune=True):
    # convert the json representation into a pandas table
    dataframe = json_normalize(json_doc)
    # set the experiment id as the index
    # dataframe.set_index('_id', inplace=True, drop=True)
    # sort the columns first by nesting depth and then lexicographically
    result = dataframe.reindex(sorted(
        dataframe.columns, key=lambda x: (len(x.split('.')), x)), axis=1)

    # Convert the column names into a hierarchical multiindex
    def remove_prefix(s, prefix):
        if s.startswith(prefix):
            return s[len(prefix):]
        else:
            return s

    def pad_tuples(t, length):
        if len(t) == length:
            return t
        elif len(t) < length:
            return t + ('',) * (length - len(t))
        else:
            raise ValueError('length should not be smaller than tuple length')


    colnames = [tuple(remove_prefix(c, 'config.').split('.'))
                for c in result.columns]
    maxlen = max([len(c) for c in colnames])
    pad_colnames = [pad_tuples(c, maxlen) for c in colnames]
    # result.columns = pd.MultiIndex.from_tuples(pad_colnames)
    if prune:
        subset = [k for k in result.keys()
                  if (not np.all(pd.isnull(result[k])) and
                      np.any(result[k] != pd.Series([result[k].iloc[0]] * len(result))))]
        for k in result.keys():
            if k not in subset:
                pass
                # print('skipping {:>20s} = {}'.format(str(k), result[k].iloc[0]))
        result = result[subset]

    return result


def get_results(collection, filter_by=None, project=None, custom_cols=None, sort='result',
                sort_direction=pymongo.DESCENDING, include_index=False, prune=True):
    # Set up a project dictionary that ensures the id and result are being
    # returned alongside whatever the user specifies
    filter_by = filter_by if filter_by else {}
    custom_cols = {} if custom_cols is None else custom_cols
    project_dict = {'result': True, '_id': include_index}
    if project is None:
        project_dict['config'] = True
    elif isinstance(project, dict):
        for k, v in project.items():
            project_dict[k] = v
    else:
        for k in project:
            project_dict[k] = True

    # get the results from the database
    all_results = []
    for r in collection.find(filter_by):
        run_summary = {k: get_by_dotted_path(r, k) for k, v in project_dict.items() if v}
        run_summary.update({k: v(r) for k, v in custom_cols.items()})
        all_results.append(run_summary)
    return convert_json_to_nice_dataframe(all_results, prune)

def delete_run(db, pro, id):
    print(id)


def delete_requete(db, project, requete, dont_delete=True):
    cursor = db[project].runs.find(requete)
    fs = gridfs.GridFS(db[project])
    delete_num = 0
    metric_num = 0
    for d in cursor:

        arti = d['artifacts']
        for i in arti:
            # print(i)
            if dont_delete:
                delete_num += 1
            else:
                fs.delete(i['file_id'])
            
        info = d['info']
        if 'metrics' in info:
            metrics = info['metrics']
            for m in metrics:
                if dont_delete:
                    metric_num += 1
                else:
                    db[project].metrics.delete_one({'_id':ObjectId(m["id"])})


    if dont_delete:

        print("Do you want to delete {} file, {} metrics and {} document ?".format(delete_num, metric_num, cursor.count()))
    else:
        db[project].runs.delete_many(requete)


def get_expe_name(db, name):
    cursor = db[name].runs.find().distinct("experiment.name")
    return list(cursor)


def explore_metric(db, name):
    cursor = db[name].metrics.find()
    print()
    for d in cursor:
        print(d)



def one_tensorboard(db, collection, id, temp_path, fs, filename):
    print(id)
    run = collection.find_one({"_id": id})
    exp = run['artifacts']
    zip_archive = None
    for file in exp:
        if file['name'] == filename:
            zip_archive = fs.get(file['file_id'])  # Gridout object
    
    if zip_archive is not None:
        zip_path = os.path.join(temp_path,str(id)+filename)

        with open(zip_path, 'wb') as f:
            f.write(zip_archive.read())

        print("On commence la decompression {}".format(id))
        unzip_path = os.path.join(temp_path,str(id))
        os.system("mkdir  {}".format(unzip_path))

        os.system("unzip {} -d {}".format(zip_path, unzip_path))
        os.system("rm {}".format(zip_path))
        print("decompression {} over".format(id))
        print(unzip_path)

    else:
        print("Not found : {}".format(id))


def list_tensorboard(db, name, list_run):

    collection = db[name].runs
    fs = gridfs.GridFS(db[name])

    filename = 'tbd.zip'
    temp_path = tempfile.mkdtemp()

    for x in list_run:
        one_tensorboard(db, collection, x, temp_path, fs, filename)



