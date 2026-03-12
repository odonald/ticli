"""Setup for ticli package."""

from setuptools import find_packages, setup

setup(
    name="tidal-cli",
    version="1.0.0",
    description="Ticli - Terminal music player for TIDAL",
    author="Ticli",
    license="MIT",
    packages=find_packages(include=["ticli", "ticli.*"]),
    python_requires=">=3.10",
    install_requires=[
        "click>=8.0",
        "rich>=13.0",
        "tidalapi>=0.8.0",
    ],
    extras_require={
        "keyring": ["keyring>=24.0"],
    },
    entry_points={
        "console_scripts": [
            "ticli=ticli.cli:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
