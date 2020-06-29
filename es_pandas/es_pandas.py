import time
import progressbar

if not progressbar.__version__.startswith('3.'):
    raise Exception('Incorrect version of progerssbar package, please do pip install progressbar2')

import numpy as np
import pandas as pd

from elasticsearch import Elasticsearch, helpers
from elasticsearch.client import IndicesClient


class es_pandas(object):
    '''Read, write and update large scale pandas DataFrame with Elasticsearch'''

    def __init__(self, *args, **kwargs):
        self.es = Elasticsearch(*args, **kwargs)
        self.ic = self.es.indices
        self.dtype_mapping = {'text': 'category', 'date': 'datetime64[ns]'}
        self.id_col = '_id'
        self.es7 = self.es.info()['version']['number'].startswith('7.')

    def to_es(self, df, index, doc_type=None, use_index=False, thread_count=2, chunk_size=1000, request_timeout=60,
              success_threshold=0.9, _op_type='index'):
        '''
        :param df: pandas DataFrame data
        :param index: full name of es indices
        :param doc_type: full name of es template
        :param use_index: use DataFrame index as records' _id
        :param delete: delete existing doc_type template if True
        :param thread_count: number of thread sent data to es
        :param chunk_size: number of docs in one chunk sent to es
        :param request_timeout:
        :param success_threshold:
        :param _op_type: elasticsearch _op_type, default 'index', choices: 'index', 'create', 'update', 'delete'
        :return: num of the number of data written into es successfully
        '''
        if self.es7:
            doc_type = '_doc'
        if not doc_type:
            doc_type = index + '_type'
        gen = helpers.parallel_bulk(self.es, (self.rec_to_actions(df, index, doc_type=doc_type, use_index=use_index, _op_type=_op_type)),
                                    thread_count=thread_count,
                                    chunk_size=chunk_size, raise_on_error=True, request_timeout=request_timeout)

        success_num = np.sum([res[0] for res in gen])
        rec_num = len(df)
        fail_num = rec_num - success_num

        if (success_num / rec_num) < success_threshold:
            raise Exception('%d records write failed' % fail_num)

        return success_num

    def get_source(self, anl, show_progress=False, count=0):
        if show_progress:
            with progressbar.ProgressBar(max_value=count) as bar:
                for i in range(count):
                    mes = next(anl)
                    yield {'_id': mes['_id'], **mes['_source']}
                    bar.update(i)
        else:
            for mes in anl:
                yield {'_id': mes['_id'], **mes['_source']}

    def infer_dtype(self, index, heads):
        if self.es7:
            mapping = self.ic.get_mapping(index=index, include_type_name=False)
        else:
            # Fix es client unrecongnized parameter 'include_type_name' bug for es 6.x
            mapping = self.ic.get_mapping(index=index)
            key = [k for k in mapping[index]['mappings'].keys() if k != '_default_']
            if len(key) < 1: raise Exception('No templates exits: %s' % index)
            mapping[index]['mappings']['properties'] = mapping[index]['mappings'][key[0]]['properties']
        dtype = {k: v['type'] for k, v in mapping[index]['mappings']['properties'].items() if k in heads}
        dtype = {k: self.dtype_mapping[v] for k, v in dtype.items() if v in self.dtype_mapping}
        return dtype


    def to_pandas(self, index, query_rule=None, heads=[], dtype={}, infer_dtype=False, show_progress=True, **kwargs):
        """
        scroll datas from es, and convert to dataframe, the index of dataframe is from es index,
        about 2 million records/min
        Args:
            es_host: es host ip:port
            query_rule:
            index: full name of es indices
            chunk_size: maximum 10000
            heads: certain columns get from es fields, [] for all fields
            dtype: dict like, pandas dtypes for certain columns
            infer_dtype: bool, default False, if true, get dtype from es template
            show_progress: bool, default True, if true, show progressbar on console
        Returns:
            DataFrame
        """
        if query_rule is None:
            query_rule = {'query': {'match_all': {}}}
        count = self.es.count(index=index, body=query_rule)['count']
        if count < 1:
            raise Exception('Empty for %s' % index)
        query_rule['_source'] = heads
        anl = helpers.scan(self.es, query=query_rule, index=index, **kwargs)
        df = pd.DataFrame(self.get_source(anl, show_progress=show_progress, count=count)).set_index('_id')
        if infer_dtype:
            dtype = self.infer_dtype(index, df.columns.values)
        if len(dtype):
            df = df.astype(dtype)
        return df

    def rec_to_actions(self, df, index, doc_type, use_index=False, _op_type='index'):
        bar = progressbar.ProgressBar(max_value=df.shape[0])
        columns = df.columns.tolist()
        if use_index and (_op_type in ['create', 'index']):
            for i, row in enumerate(df.itertuples(name=None, index=use_index)):
                bar.update(i)
                _id = row[0]
                record = dict(zip(columns, [None if pd.isna(r) else r for r in row[1:]]))
                action = {
                    '_op_type': _op_type,
                    '_index': index,
                    '_type': doc_type,
                    '_id': _id,
                    '_source': record}
                yield action
        elif (not use_index) and (_op_type == 'index'):
            for i, row in enumerate(df.itertuples(name=None, index=use_index)):
                bar.update(i)
                record = dict(zip(columns, [None if pd.isna(r) else r for r in row]))
                action = {
                    '_op_type': _op_type,
                    '_index': index,
                    '_type': doc_type,
                    '_source': record}
                yield action
        elif _op_type == 'update':
            for i, row in enumerate(df.itertuples(name=None, index=True)):
                bar.update(i)
                _id = row[0]
                record = dict(zip(columns, [None if pd.isna(r) else r for r in row[1:]]))
                action = {
                    '_op_type': _op_type,
                    '_index': index,
                    '_type': doc_type,
                    '_id': _id,
                    'doc': record}
                yield action
        elif _op_type == 'delete':
            for i, _id in enumerate(df.index.values.tolist()):
                bar.update(i)
                action = {
                    '_op_type': _op_type,
                    '_index': index,
                    '_type': doc_type,
                    '_id': _id
                }
                yield action
        else:
            raise Exception('[%s] action with %s using index not supported' %(_op_type, '' if use_index else 'not'))

    def init_es_tmpl(self, df, doc_type, delete=False, shards_count=2, wait_time=5):
        tmpl_exits = self.es.indices.exists_template(name=doc_type)
        if tmpl_exits and (not delete):
            return
        columns_body = {}

        if isinstance(df, pd.DataFrame):
            iter_dict = df.dtypes.to_dict()
        elif isinstance(df, dict):
            iter_dict = df
        else:
            raise Exception('init tmpl type is error, only accept DataFrame or dict of head with type mapping')
        for key, data_type in iter_dict.items():
            type_str = getattr(data_type, 'name', data_type).lower()
            if 'int' in type_str:
                columns_body[key] = {'type': 'long'}
            elif 'datetime' in type_str:
                columns_body[key] = {'type': 'date'}
            elif 'float' in type_str:
                columns_body[key] = {'type': 'float'}
            else:
                columns_body[key] = {'type': 'keyword', 'ignore_above': '256'}

        tmpl = {
            'template': '%s*' % doc_type,
            'settings': {
                'index': {
                    'refresh_interval': '5s',
                    'number_of_shards': shards_count,
                    'number_of_replicas': '1',
                    'merge': {
                        'scheduler': {
                            'max_thread_count': '1'
                        }
                    }
                }
            }
        }
        if self.es7:
            tmpl['mappings'] ={'properties': columns_body}
        else:
            tmpl['mappings'] = {'_default_':
                             {'properties': columns_body}
                         }
        if tmpl_exits and delete:
            self.es.indices.delete_template(name=doc_type)
            print('Delete and put template: %s' % doc_type)
        self.es.indices.put_template(name=doc_type, body=tmpl)
        print('New template %s added' % doc_type)
        time.sleep(wait_time)
