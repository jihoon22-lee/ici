"""Planted security defects for the stable/next differential."""


def run_user_expression(expr: str):
    return eval(expr)


def load_blob(blob: bytes):
    import pickle

    return pickle.loads(blob)


def shell_out(command: str):
    import subprocess

    return subprocess.run(command, shell=True)
