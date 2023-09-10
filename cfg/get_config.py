"""
Module for fetching configuration data from various sources.

This module provides functionalities to retrieve configuration values from
several places: environment variables, TOML configuration files, and dotenv files.
The actual source and its priority can be defined by the user.

Functions:
    - get_config: The main function to fetch configuration values.

Exceptions:
    - ConfigItemNotFound: Raised when the desired configuration is not found in any of the sources.
"""

import os
from pathlib import Path
from typing import Literal, Generic, TypeVar, Union, Protocol, Mapping, Any

import dotenv
import toml

DEFAULT_HOME = Path.home()

T = TypeVar['T']
E = TypeVar['E']


class MakeFileNameFunction(Protocol):
    """
    Protocol to define a function that creates a file name where the config item should be located given the
    application name, the configuration section and the item name.
    """
    def __call__(self, application: str, section: str, item: str) -> str:
        ...


class ReadConfigFileFunction(Protocol):
    """
    A protocol that defines a function which, given a path to a file, reads the contents of that file into a mapping
    (like a dict).
    """
    def __call__(self, path: str) -> Mapping[str, Any]:
        ...


class Result(Generic[T, E]):
    """A generic, Rust-inspired Result class used for returning either a successful result or a list of errors."""

    __ok: T | None
    __err: list[E] | None

    @classmethod
    def ok(cls, result: T):
        return cls('ok', result)

    @classmethod
    def error(cls, *errors: Union['Result', Exception]):
        errors_ = []
        for maybe_err in errors:
            if isinstance(maybe_err, Exception):
                errors_.append(Result.error(maybe_err))
            else:
                assert isinstance(maybe_err, Result), 'Expected either an Exception or Result type.'
        return cls('error', errors_)

    def __init__(self, status: Literal['ok', 'error'], contents: T | list[E] | None):
        if status == 'ok':
            self.__ok = contents
            self.__err = None
        else:
            self.__err = contents
            self.__ok = None

    def status_and_value(self) -> Union[(Literal['ok'], T), (Literal['error'], E)]:
        if self.__ok:
            return 'ok', self.__ok
        else:
            return 'error', self.__err

    def is_err(self):
        return bool(self.__err)

    def is_ok(self):
        return bool(self.__ok)


class GetConfigFunction(Protocol):
    """
    A protocol that defines a function that fetches a configuration item from some source.
    """
    def __call__(self, application: str, section: str, item: str, home: str | None) -> Result[Any, Exception]:
        ...


class ConfigItemNotFound(Exception):
    """Exception raised when a configuration item is not found."""
    def __init__(self, err_result: Result):
        status, errors = err_result.status_and_value()
        assert status == 'error'
        error_messages = [str(error) for error in errors]
        message = 'Config item not found due to the following errors:\n' + "\n".join(error_messages)
        super().__init__(message)


def __find_in_files(application: str,
                    section: str,
                    item: str,
                    home: str,
                    make_file_name: MakeFileNameFunction,
                    read_config_file: ReadConfigFileFunction) -> Result[Any, Exception]:
    """
    Search for configuration data in local and global directories based on the provided application, section, and
    item names.

    This function looks in two directories for a configuration file:
    - The 'config' subdirectory of the current working directory.
    - The 'config' subdirectory of the provided `home` directory.

    The exact file name and its sub-path within these directories are determined by the `make_file_name` function.

    :param application: The name of the application for which the configuration is sought.
    :param section: The section within the application's configuration to look into. Can be None.
    :param item: The specific object or key within the section to retrieve.
    :param home: The base directory for global configurations.
    :param make_file_name: A function that, given the application, section, and object, produces the desired file name.
    :return: If the object is found, returns a Result with 'ok' and the corresponding data. If not, or if any exceptions
             occur, returns a Result with 'error' and a list of exceptions.
    """
    directories = [
        os.path.join(os.getcwd(), 'config'),
        os.path.join(home, 'config')
    ]

    file_name = make_file_name(application, section, item)
    errors = []

    for directory in directories:
        full_path = os.path.join(directory, application, file_name)
        try:
            data = read_config_file(full_path)
            if item in data:
                return Result.ok(data[item])
        except Exception as e:
            errors.append(Result.error(e))

    return Result('error', errors)


def __get_config_from_toml(application: str, section: str, item: str, home: str) -> Result[Any, Exception]:
    """
    Fetch configuration data from a TOML file.

    This function retrieves the specified item from a TOML file, given the application and section.
    The search prioritizes the local directory over the global home directory.

    :param application: Name of the application.
    :param section: Section of the configuration.
    :param item: Specific configuration item to fetch.
    :param home: Home directory to search for global configurations.
    :return: The configuration value if found or an error.
    """
    def make_file_name(_app: str, sec: str, _obj: str) -> str:
        return f'{sec}.toml'

    def read_config_file(file_name: str) -> Mapping[str, Any]:
        with open(file_name, encoding='utf-8') as fh:
            data = toml.load(fh)
            return data

    return __find_in_files(application, section, item, home,
                           make_file_name=make_file_name,
                           read_config_file=read_config_file)


def __get_config_from_dotenv(application: str, section: str, item: str, home: str) -> Result[Any, Exception]:
    """
    Fetch configuration data from a dotenv file.

    This function retrieves the specified item from a dotenv file, given the application and section.
    The search prioritizes the local directory over the global home directory.

    :param application: Name of the application.
    :param section: Section of the configuration.
    :param item: Specific configuration item to fetch.
    :param home: Home directory to search for global configurations.
    :return: The configuration value if found or an error.
    """
    def make_file_name(_app: str, sec: str, _obj: str) -> str:
        return f'.env.{sec}'

    def read_config_file(file_name: str) -> Mapping[str, Any]:
        return dotenv.dotenv_values(file_name)

    attempt_1 = __find_in_files(application, section, item, home,
                                make_file_name=make_file_name,
                                read_config_file=read_config_file)

    if attempt_1.is_ok():
        return attempt_1
    else:
        return __find_in_files(application, section, item, home,
                               make_file_name=lambda _app, _sec, _obj: '.env',
                               read_config_file=read_config_file)


def __get_config_from_env(application: str, section: str, item: str, _home: str) -> Result[Any, Exception]:
    """
    Fetch configuration data from an environment variable.

    The name of the environment variable is constructed from the application, section, and item,
    with all in uppercase and separated by underscores.

    :param application: Name of the application.
    :param section: Section of the configuration.
    :param item: Specific configuration item to fetch.
    :return: The configuration value if found or an error.
    """
    env_variable_name = f'{application.upper()}_{section.upper()}_{item.upper()}'
    try:
        return Result('ok', os.environ[env_variable_name])
    except Exception as e:
        return Result('error', [e])


def get_config(item: str, section: str = 'DEFAULT', application: str = 'DEFAULT', home: str = DEFAULT_HOME,
               priority: list[Literal['config file', '.env file', 'env variable']] | None = None):
    """
    Fetch a configuration item.

    This function attempts to retrieve a configuration item from various sources, based on a priority list.
    The user can specify the priority of the sources, otherwise a default priority is used.

    :param item: The configuration item's name.
    :param section: The section in which the item resides (default to 'DEFAULT').
    :param application: The application name for which the configuration is sought (default to 'DEFAULT').
    :param home: Base directory for global configurations (default to the user's home directory).
    :param priority: A list indicating the priority of sources. If not provided, a default priority is used.
    :return: The configuration value if found.
    :raise ConfigItemNotFound: If the configuration item is not found in any of the sources.
    """

    if not priority:
        priority = ['env variable', '.env file', 'config file']

    dispatch: Mapping[str, GetConfigFunction] = {
        'env variable': __get_config_from_env,
        '.env file': __get_config_from_dotenv,
        'config file': __get_config_from_toml,
    }

    for p in priority:
        fn = dispatch[p]
        result = fn(application, section, item, home)
        status, result_value = result.status_and_value()
        if status == 'ok':
            return result_value
        else:
            raise ConfigItemNotFound(result)
