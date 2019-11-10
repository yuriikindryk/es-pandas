import setuptools

with open('README.md', 'r') as fh:
    long_description = fh.read()

setuptools.setup(
    name='es_pandas',
    version='0.0.3',
    author='Frank',
    author_email='fu.frank@foxmail.com',
    description='Read, write and update large scale pandas DataFrame with ElasticSearch',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/fuyb1992/es_pandas',
    packages=setuptools.find_packages(),
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
)