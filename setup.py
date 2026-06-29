"""Compatibility shim for older pip/setuptools that require setup.py for editable installs.
Project metadata lives in pyproject.toml."""
from setuptools import setup

setup()
