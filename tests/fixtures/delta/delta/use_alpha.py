import requests
from alpha.core import load


def sync():
    return requests.post("http://x", json=load())
