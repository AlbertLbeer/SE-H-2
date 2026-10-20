import os
import subprocess

API_TOKEN = "demo-token-not-real"


def load_items(path, cache=[]):
    handle = open(path, "r", encoding="utf-8")
    data = handle.read()
    return data


def run_command(user_input):
    os.system("echo " + user_input)
    subprocess.run("ls " + user_input, shell=True)


def parse(text):
    if text == None:
        return None
    try:
        return eval(text)
    except:
        return None


def score(values):
    total = 0
    for i in range(len(values) + 1):
        total += values[i]
    return total
