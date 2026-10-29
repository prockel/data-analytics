.PHONY: test check notebook

test:
	python -m unittest discover -s tests -v

check: test
	python -m pip check
	firstblood --help

notebook:
	jupyter nbconvert --to notebook --execute notebooks/analysis.ipynb --output analysis.executed.ipynb --output-dir .local
