from setuptools import setup
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


setup(
    name="llm-wiki-starter",
    version=VERSION,
    description="A local markdown-based LLM wiki starter with sync, ingest, query, and lint workflows.",
    packages=["scripts"],
    install_requires=[
        "anthropic>=0.54.0",
        "python-dotenv>=1.0.1",
        "pydantic>=2.8.2",
        "pypdf>=5.4.0",
    ],
    extras_require={
        "dev": [
            "pytest>=8.3.0",
            "wheel>=0.45.0",
        ]
    },
    entry_points={
        "console_scripts": [
            "llm-wiki=scripts.cli:main",
        ]
    },
)
