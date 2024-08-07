# -*- coding: utf-8 -*- #

import os
from dotenv import load_dotenv
import pickle
import time

import requests
import pandas as pd
import getpass
from datetime import datetime

BASE_URL = 'https://dataapi.octoparse.com/'
ADV_BASE_URL = 'https://advancedapi.octoparse.com/'

# urls for china
CHINA_BASE_URL = 'https://dataapi.bazhuayu.com/'
CHINA_ADV_BASE_URL = 'https://advancedapi.bazhuayu.com/'

# Helper Methods


def _post_request(url, token, params=None, body=None):
    """
    Send a requests.post request
    :param url: URL
    :param token: authorization token
    :param params: URL Parameters
    :param body: body to be sent with request
    :return: json of response
    """
    headers = {
        'Authorization': 'bearer ' + token
    }

    if body is None:
        res = requests.post(url, headers=headers, params=params)
    else:
        res = requests.post(url, headers=headers, params=params, data=body)
    if res.status_code == 200:
        pass
    else:
        pass
    return res.json()


def _get_request(url, token, params=None):
    """
    Send a requests.get request
    :param url: API url
    :param token: API token
    :param params: URL Parameters
    :return: Response from server
    """
    headers = {
        'Authorization': 'bearer ' + token
    }
    if params is None:
        res = requests.get(url, headers=headers)
    else:
        res = requests.get(url, headers=headers, params=params)

    if res.status_code == 200:
        pass
    else:
        pass
    return res.json()


def _get_credentials():
    """
    read .env file and load env variables
    :return:
    """
    load_dotenv()
    return os.getenv('OCTOPARSE_USERNAME'), os.getenv('OCTOPARSE_PASSWORD')


class Octoparse:
    """
    Octoparse class to act as octoparse api client.
    All the requests can be made through this class.
    """

    def __init__(self, advanced_api=False, china=False):
        """
        Initialize the object
        :param advanced_api: whether use advanced api or not
        :param china: access from china or not
        """

        self.token_entity = None
        if advanced_api:
            if china:
                self.base_url = CHINA_ADV_BASE_URL
            else:
                self.base_url = ADV_BASE_URL
        else:
            if china:
                self.base_url = CHINA_BASE_URL
            else:
                self.base_url = BASE_URL
        self._token_file = 'octoparse_token.pickle'
        self._read_token_file()

    def __del__(self):
        self._save_token_file()

    def _read_token_file(self):
        """
        Read token pickle file from disk
        """
        if os.path.exists(self._token_file):
            with open(self._token_file, 'rb') as token:
                self.token_entity = pickle.load(token)
        else:
            self.log_in()

    def _save_token_file(self):
        """
        Save token pickle file to disk
        """
        if self.token_entity is None or 'access_token' not in self.token_entity:
            return
        with open(self._token_file, 'wb') as token:
            pickle.dump(self.token_entity, token)

    def _get_access_token(self):
        """
        Return the valid access token
        if expired then first refresh the token
        :return: access token string
        """
        if self.token_entity is None:
            self.log_in()
        else:
            # check if token expired
            timedelta = datetime.now() - self.token_entity['datetime']
            if timedelta.total_seconds() > float(self.token_entity['expires_in']):
                self.refresh_token()
        return self.token_entity['access_token']

    def _get_url(self, path):
        """
        Returns the absolute url
        :param path: relative url path
        :return: absolute url
        """
        return self.base_url + path

    def log_in(self):
        """
        Login & get an access token
        :return: token entity
        """
        username, password = _get_credentials()
        if not username or not password:
            username = input("Enter Octoparse Username: ")
            password = getpass.getpass('Password: ')

        url = self.base_url + 'token'
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        data = {
            'username': username,
            'password': password,
            'grant_type': 'password'
        }

        response = requests.post(url, data=data, headers=headers)
        token_entity = response.json()
        print(token_entity)

        if 'access_token' in token_entity:
            self.token_entity = token_entity
            # add time to token
            token_entity['datetime'] = datetime.now()
            self._save_token_file()
            return token_entity
        else:
            exit(1)

    def refresh_token(self):
        """
        Refresh the access token using the refresh token
        :return: refreshed token entity
        """
        if self.token_entity is None or 'refresh_token' not in self.token_entity:
            raise ValueError("No refresh token available")

        refresh_token = self.token_entity['refresh_token']
        content = f"refresh_token={refresh_token}&grant_type=refresh_token"

        headers = {'Content-Type': 'application/x-www-form-urlencoded'}

        response = requests.post(self.base_url + 'token', data=content, headers=headers)
        if response.status_code != 200:
            raise ValueError(f"Failed to refresh token. Status code: {response.status_code}")

        if response.status_code == 200:
            refreshed_token_entity = response.json()

            if 'access_token' not in refreshed_token_entity:
                raise ValueError("Access token not found in refreshed token entity")

            self.token_entity = refreshed_token_entity
            return refreshed_token_entity
        elif response.status_code == 400:
            raise ValueError("Incorrect username or password")
        elif response.status_code == 401:
            raise ValueError("Access Token is invalid because it is expired or unauthorized. Please get a new token.")
        elif response.status_code == 403:
            raise ValueError(
                "Permission denied. Please upgrade to Standard Plan to use Data API; upgrade to Professional Plan to use Advanced API.")
        elif response.status_code == 404:
            raise ValueError("The HTTP request is not recognized. Please request with the correct URL.")
        elif response.status_code == 405:
            raise ValueError("The HTTP method is not supported. Please use the method supported by the interface.")
        elif response.status_code == 429:
            raise ValueError(
                "The request frequency has exceeded the limit. Please reduce access frequency to less than 20 times per second.")
        elif response.status_code == 503:
            raise ValueError("The server is temporarily unavailable. Please try again later.")
        else:
            raise ValueError(f"Failed to refresh token. Status code: {response.status_code}")

    def is_task_running(self, task_id, time_gap=5):
        """
        Check if a Task is currently running. This isn't provided in Standard API.
        We can detect if the No. of rows in Task increases over time_gap seconds.
        :param task_id:  octoparse task id
        :param time_gap: Time interval to check between
        :return: Boolean True or False
        """

        params = {
            'taskId': task_id,
            'offset': 0,
            'size': 10
        }

        path = 'api/alldata/GetDataOfTaskByOffset'

        resp = _get_request(self._get_url(path),
                            self._get_access_token(),
                            params=params
                            )
        total1 = resp.get('data', {}).get('total', 0)

        time.sleep(time_gap)

        resp = _get_request(self._get_url(path),
                            self._get_access_token(),
                            params=params
                            )
        total2 = resp.get('data', {}).get('total', 0)

        if total1 == total2:
            return False
        else:
            return True

    def get_task_data(self, task_id, size=1000, offset=0):
        """
        Fetch data for a task id.
        This will fetch all the data rows present for the task starting from offset
        till the end of data. It may be a blocking call. If you are interested in
        only a subset of data get_data_by_offset() may be better option.
        This method is only used to get data but will not affect the status of data.
        (Non-exported data will still remain as non-exported)

        :param task_id: octoparse task id
        :param size: chunk size to be fetched in each request
        :param offset: offset of data to be fetched from start
        :return: list of data dict
        """

        offset = offset
        data_list = []

        path = 'api/alldata/GetDataOfTaskByOffset'

        if size > 1000:
            size = 1000

        while True:
            params = {
                'taskId': task_id,
                'offset': offset,
                'size': size
            }

            data = _get_request(self._get_url(path),
                                self._get_access_token(),
                                params=params
                                )
            data_list += data['data'].get('dataList', [])

            if data['data']['restTotal'] != 0:
                offset = data['data']['offset']
            else:
                break
        return data_list

    def get_task_data_df(self, task_id):
        """
        Fetch data for a task id & returns it as pandas.DataFrame
        This will fetch all the data rows present for the task starting from offset
        till the end of data. It may be a blocking call. If you are interested in
        only a subset of data get_data_by_offset() may be better option.

        :param task_id: octoparse task id
        :return: pandas.DataFrame data
        """

        data = self.get_task_data(task_id)
        df = pd.DataFrame.from_dict(data)

        return df

    def get_data_by_offset(self, task_id, size=1000, offset=0):
        """
        Fetch data for a task starting from the offset. Only rows equal to less than
        size will be fetched & returned.

        Offset should default to 0 (offset=0), and size∈[1,1000] for
        making the initial request. The offset returned (could be any value greater
        than 0) should be used for making the next request. For example, if a task
        has 1000 data rows, using parameter: offset = 0, size = 100 will return the
        first 100 rows of data and the offset X (X can be any random number greater
        than or equal to 100). When making the second request, user should use the
        offset returned from the first request, offset = X, size = 100 to get the
        next 100 rows of data (row 101 to 200) as well as the new offset to use for
        the request follows.
        This method is only used to get data but will not affect the status of data.
        (Non-exported data will still remain as non-exported)
        :param task_id: octoparse task id
        :param size: rows to be fetched (max: 1000)
        :param offset: offset of data to be fetched from start
        :return: list of data dict
        """
        data = list()

        path = 'api/alldata/GetDataOfTaskByOffset'

        if size > 1000:
            size = 1000

        params = {
            'taskId': task_id,
            'offset': offset,
            'size': size
        }

        response = _get_request(self._get_url(path),
                            self._get_access_token(),
                            params=params
                            )
        if 'data' in response:
            data = response['data'].get('dataList', [])
        return data

    def get_task_data_generator(self, task_id, size=1000, offset=0):
        """
        Fetch data for a task id.
        This will fetch all the data rows present for the task starting from offset
        till the end of data.
        This is a generator so can be easily used in a loop.
        This method is only used to get data but will not affect the status of data.
        (Non-exported data will still remain as non-exported)

        :param task_id: octoparse task id
        :param size: chunk size to be fetched in each request
        :param offset: offset of data to be fetched from start
        :return: list of data dict
        """

        offset = offset

        path = 'api/alldata/GetDataOfTaskByOffset'

        while True:
            params = {
                'taskId': task_id,
                'offset': offset,
                'size': size
            }

            response = _get_request(self._get_url(path),
                                self._get_access_token(),
                                params=params
                                )
            yield response['data'].get('dataList', [])

            if response['data']['restTotal'] != 0:
                offset = response['data']['offset']
            else:
                return

    def clear_task_data(self, task_id):
        """
        Clear data of a task
        :param task_id: octoparse task id
        :return: response from api
        """

        path = 'api/task/removeDataByTaskId?taskId=' + task_id
        response = _post_request(self._get_url(path), self._get_access_token())
        return response

    def list_all_task_groups(self):
        """
        List All Task Groups
        :return: list -- all task groups
        """

        path = 'api/taskgroup'

        task_groups = list()
        response = _get_request(self._get_url(path), self._get_access_token())

        if 'data' in response:
            task_groups = response['data']
        return task_groups

    def list_all_tasks_in_group(self, group_id):
        """
        List All Tasks in a Group
        :param group_id: a task group id
        :return: list -- all tasks in a group
        """

        path = 'api/task'

        params = {
            'taskgroupId': group_id
        }

        task_list = list()
        response = _get_request(self._get_url(path), self._get_access_token(), params=params)

        if 'data' in response:
            task_list = response['data']
        return task_list

    def get_not_exported_data(self, task_id, size=1000):
        """
        This returns non-exported data. Data will be tagged status = exporting
        (instead of status=exported) after the export.
        This way, the same set of data can be exported multiple times using this method.
        If the user has confirmed receipt of the data and wish to update
        data status to ‘exported’, please call method update_data_status().
        :param task_id: octoparse task id
        :param size: The amount of data rows(range from 1 to 1000)
        :return: json -- task dataList and relevant information
        """

        path = 'api/notexportdata/gettop'

        params = {
            'taskId': task_id,
            'size': size
        }

        data = list()
        response = _get_request(self._get_url(path), self._get_access_token(), params=params)

        if 'data' in response:
            data = response['data']
        return data

    def update_data_status(self, task_id):
        """
        This updates data status from ‘exporting’ to ‘exported’.
        :return: string -- remind message(include error if exists)
        """
        path = 'api/notexportdata/update'

        params = {
            'taskId': task_id
        }
        response = _post_request(self._get_url(path), self._get_access_token(), params=params)

        return response

    # below are Advanced API access functions

    def get_task_status(self, task_id_list=[]):
        """
        This returns status of multiple tasks.
        :param task_id_list: List of task's id
        :return: List of status'
        """
        path = 'api/task/getTaskStatusByIdList'

        params = {
            "taskIdList": task_id_list
        }

        response = _post_request(self._get_url(path), self._get_access_token(), params=params)

        return response

    def get_task_params(self, task_id, name):
        """
        This returns the different parameters for a specific task,
        for example, the URL from ‘Go To The Web Page’ action,
        text value from ‘Enter Text’ action and text list/URL
        list from ‘Loop Item’ action.

        :param task_id: Task ID
        :param name: Configuration parameter name (navigateAction1.Url,loopAction1.UrlList,loopAction1.TextList, etc.)
        :return: Task parameters values (or value arrays) and request status
        """
        path = 'api/task/GetTaskRulePropertyByName'

        params = {
            "taskId": task_id,
            'name': name
        }

        response = _post_request(self._get_url(path), self._get_access_token(), params=params)

        return response

    def update_task_param(self, task_id, name, value):
        """
        Use this method to update task parameters (currently only
        available to updating URL in ‘Go To The Web Page’ action,
        text value in ‘Enter Text’ action,
        and text list/URL list in ‘Loop Item’ action).

        :param task_id: Task ID
        :param name: parameters name
        :param value: parameters value
        :return: The task parameter has been updated successfully or not.
        """

        path = 'api/task/updateTaskRule'

        params = {
            "taskId": task_id,
            'name': name,
            'value': value
        }

        response = _post_request(self._get_url(path), self._get_access_token(), params=params)

        return response

    def add_url_text_to_loop(self, task_id, name, value):
        """
        Use this method to add new URLs/text to an existing loop.

        Note: For updating text list/URL list values, please use
        [‘text1’, ’text2’, ’text3’,’textN’] to represent N items.

        :param task_id: Task ID
        :param name: parameters name
        :param value: parameters value
        :return: The new parameter values have been added successfully or not.
        """

        path = 'api/task/AddUrlOrTextToTask'

        params = {
            "taskId": task_id,
            'name': name,
            'value': value
        }

        response = _post_request(self._get_url(path), self._get_access_token(), params=params)

        return response

    def start_task(self, task_id):
        """
        Start Running Task
        :param task_id: Task ID
        :return: Status Codes ("data" parameter in response content): 1 = Task starts successfully,
        2 = Task is running,
        5 = Task Configuration is incorrect,
        6 = Permission denied, 100 = Other Error
        """
        path = 'api/task/startTask'

        params = {
            "taskId": task_id
        }

        response = _post_request(self._get_url(path), self._get_access_token(), params=params)

        return response

    def stop_task(self, task_id):
        """
        Stop Running Task
        :param task_id: Task ID
        :return: The task has been stopped successfully or not.
        """
        path = 'api/task/stopTask'

        params = {
            "taskId": task_id
        }

        response = _post_request(self._get_url(path), self._get_access_token(), params=params)

        return response
