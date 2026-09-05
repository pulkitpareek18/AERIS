from setuptools import find_packages, setup


setup(
    name="aeris-ml",
    version="0.1.0",
    description="Dataset ingestion and preprocessing foundations for AERIS Wi-Fi CSI models",
    package_dir={"": "src"},
    packages=find_packages("src"),
    python_requires=">=3.9",
    install_requires=["numpy>=1.23", "PyYAML>=6.0"],
    extras_require={"dev": ["pytest>=7.0"]},
    entry_points={"console_scripts": ["aeris-ml=aeris_ml.cli:main"]},
    data_files=[("configs", ["configs/datasets.yaml"])],
)
