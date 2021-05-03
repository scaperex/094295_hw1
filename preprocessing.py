from ast import literal_eval
import argparse
import numpy as np
import pandas as pd
import csv
from pandas.plotting import scatter_matrix
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats
from sklearn.preprocessing import StandardScaler, MinMaxScaler, OneHotEncoder, MultiLabelBinarizer
from sklearn.impute import SimpleImputer
import os
import pickle
from datetime import datetime
from sklearn.pipeline import Pipeline
import joblib
from sklearn.compose import ColumnTransformer, make_column_transformer
from config import parsed_train_path, parsed_test_path, scaler_path, encoder_path, train_path, test_path, max_values
from collections import Counter


def treat_dict_column(data, old_col_name, new_col_name, key):
    data[old_col_name].fillna('{}', inplace = True)
    data[old_col_name] = data[old_col_name].apply(literal_eval)
    data[new_col_name] = data[old_col_name].apply(pd.Series)[key]
    data.drop(old_col_name, inplace=True, axis=1)
    return data

def treat_list_of_dicts_column_to_one_list(data, col_name, key):
    data[col_name].fillna('[]', inplace=True)
    data[col_name] = data[col_name].apply(literal_eval)
    data[col_name] = data[col_name].apply(lambda x: [d[key] for d in x] if x != [] else ["nan"])
    return data

def apply_filter(x, key, filter_by, filter_list):
    res = [d[key] for d in x if d[filter_by] in filter_list]
    res = res if res != [] else["nan"]
    return res

def treat_list_of_dicts_column_to_multiple_lists(data, col_name, keys):
    data[col_name].fillna('[]', inplace=True)
    data[col_name] = data[col_name].apply(literal_eval)
    if col_name == "crew":
        key = keys[0]
        filter_by = keys[1]
        data[col_name] = data[col_name].apply(lambda x: apply_filter(x,key,filter_by,["Producer", "Director", "Writer"]))
    else:
        data[col_name] = data[col_name].apply(lambda x: [[d[key] for key in keys] for d in x])
    return data

def encode_date(data, col, max_val):
    data[col + '_sin'] = np.sin(2 * np.pi * data[col]/max_val)
    data[col + '_cos'] = np.cos(2 * np.pi * data[col]/max_val)
    return data

def apply_filter_most_common(x, filter_list):
    res = set(x).intersection(filter_list)
    res = list(res) if res != set() else["nan"]
    return res

def save_most_common(data, col_name, k):
    words = Counter(c for clist in data[col_name] for c in clist)
    most_common = words.most_common(k)
    most_common_keys = [key[0] for key in most_common]
    data[col_name] = data[col_name].apply(lambda x: apply_filter_most_common(x,most_common_keys))
    return data

def parse_data(data, max_order=2, train=True):
    data_label = data["revenue"]
    # remove features - unreasonable & 1 uniqe value features
    data.drop(["backdrop_path", "homepage", "imdb_id", "status", "poster_path", "revenue"], inplace=True, axis=1)

    # Flatten nested objects
    data = treat_dict_column(data, "belongs_to_collection", "collection_name", "name")
    data = treat_list_of_dicts_column_to_multiple_lists(data, 'cast', ['name', 'gender'])
    data = treat_list_of_dicts_column_to_one_list(data, 'genres', 'name')
    data = treat_list_of_dicts_column_to_one_list(data, 'spoken_languages', 'iso_639_1')
    data = treat_list_of_dicts_column_to_one_list(data, 'production_countries', 'iso_3166_1')
    data = treat_list_of_dicts_column_to_multiple_lists(data, 'crew', ['name', 'job'])
    data = treat_list_of_dicts_column_to_one_list(data, 'production_companies', 'name')

    multi_dummy_columns = ["genres", "spoken_languages", "production_countries", "crew", "production_companies"]
    for col in multi_dummy_columns:
        data = save_most_common(data, col, max_values)

    # Save only the first max_order cast info a split it into 2*max_order columns
    for i in range(max_order):
        data[f'cast_{i}_name'] = data['cast'].apply(lambda x: x[i][0] if len(x) > i else None)
        data[f'cast_{i}_gender'] = data['cast'].apply(lambda x: x[i][1] if len(x) > i else None)
    data.drop("cast", inplace=True, axis=1)

    # TODO embedding features
    #data = treat_list_of_dicts_column_to_one_list(data, 'Keywords', 'name')
    embedding_features = ["original_title", "overview", "title", "tagline", "Keywords"]
    data.drop(embedding_features, inplace=True, axis=1)

    #
    # data['cast_size'] = data['cast'].apply(lambda x: len(x))
    # data['cast_size'].hist(bins=50)
    # plt.savefig(f"cast_size.png", bbox_inches='tight')
    # data.drop("cast_size", inplace=True, axis=1)

    # Convert Bool to 1 and 0
    data['video'].fillna(0, inplace=True)
    data['video'] = data['video'].astype(int)

    # Convert release_date to month,day,month_sin,month_cos,day_sin,day_cos,weekend
    data["release_date"].fillna(method="pad", inplace=True)
    data['month'] = data.release_date.dt.month
    data = encode_date(data, 'month', 12)
    data['day'] = data.release_date.dt.day
    data = encode_date(data, 'day', 365)
    day_names = data.release_date.dt.day_name()
    data['is_weekend'] = day_names.apply(lambda x: 1 if x in ['Saturday', 'Sunday'] else 0)
    data.drop(["release_date"], inplace=True, axis=1)
    #data['release_date'] = data['release_date'].apply(lambda x: datetime.timestamp(x))

    numerical_columns = ["popularity", "budget", "runtime", "vote_average", "vote_count",
                         "month_sin", "month_cos", "day_sin", "day_cos", "month", "day"]
    dummy_columns = ["collection_name", "original_language"]
    for i in range(max_order):
        dummy_columns.append(f'cast_{i}_name')
        dummy_columns.append(f'cast_{i}_gender')

    if train:
        # Normalized numerical features
        pipe = Pipeline([('imputer', SimpleImputer(missing_values=np.nan, strategy='mean')),
                         ('standard_scaler', StandardScaler()), ('minmax_scaler', MinMaxScaler(clip=True))])
        pipe.fit(data[numerical_columns].to_numpy())
        joblib.dump(pipe, scaler_path)

        # One hot Encoders fit
        encoders = {}
        for col_d in dummy_columns:
            enc = OneHotEncoder(handle_unknown='ignore')
            enc.fit(data[col_d].to_numpy().reshape(-1, 1))
            encoders[col_d] = enc
        for col_m in multi_dummy_columns:
            enc = MultiLabelBinarizer()
            enc.fit((data[col_m]))
            encoders[col_m] = enc
        joblib.dump(encoders, encoder_path)
    else:
        pipe = joblib.load(scaler_path)
        encoders = joblib.load(encoder_path)

    # Normalized numerical features
    data[numerical_columns] = pipe.transform(data[numerical_columns].to_numpy())

    # One hot Encoders transform
    data_arr_dummies = []
    for col_d in dummy_columns:
        enc = encoders[col_d]
        data_arr_dummies.append(enc.transform(data[col_d].to_numpy().reshape(-1, 1)).toarray())
        data.drop(col_d, inplace=True, axis=1)
    for col_m in multi_dummy_columns:
        enc = encoders[col_m]
        data_arr_dummies.append(enc.transform((data[col_m])))
        data.drop(col_m, inplace=True, axis=1)
    data_arr = np.concatenate([data.to_numpy()]+data_arr_dummies, axis=1)

    return data_arr, data_label.to_numpy(), data.index

def create_sample(data):
    data = data.head(len(data.columns))
    data = data.copy(deep=True)
    for i in range(len(data.columns)):
        data.iloc[i,i] = None
    data.to_csv("sample.tsv", sep="\t")


if __name__ == '__main__':
    parse_train = True
    parse_test = True
    # TODO competition adjustments - no label

    # TODO check none values on the final features
    # create_sample(pd.read_csv(test_path, sep="\t", index_col='id', parse_dates=['release_date']))
    # sample_data = pd.read_csv("sample.tsv", sep="\t", index_col='id', parse_dates=['release_date'])
    # parsed_sample_data, parsed_sample_label, parsed_sample_index = parse_data(sample_data, train=True)

    if parse_train:
        train_data = pd.read_csv(train_path, sep="\t", index_col='id', parse_dates=['release_date'])
        parsed_train_data, parsed_train_label, parsed_train_index = parse_data(train_data, train=True)
        with open(parsed_train_path, 'wb') as f:
            np.save(f, parsed_train_data)
            np.save(f, parsed_train_label)
            np.save(f, parsed_train_index)
        print(f"Number of features {train_data.shape[1]}")

    if parse_test:
        test_data = pd.read_csv(test_path, sep="\t", index_col='id', parse_dates=['release_date'])
        parsed_test_data, parsed_test_label, parsed_test_index = parse_data(test_data, train=False)
        with open(parsed_test_path, 'wb') as f:
            np.save(f, parsed_test_data)
            np.save(f, parsed_test_label)
            np.save(f, parsed_test_index)

