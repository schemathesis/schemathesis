# Run testing AioHTTP app on the given port
PYTHONPATH=$(pwd)/test/apps python test/apps/__init__.py "$@"
