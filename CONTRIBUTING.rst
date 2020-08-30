Contributing to Schemathesis
============================

Welcome! We are very happy that you're reading this!

Your feedback and your experience are important for the project :)

.. contents::
   :depth: 2
   :backlinks: none

.. _feedback:

Feature requests and feedback
-----------------------------

If you'd like to suggest a feature, feel free to `submit an issue <https://github.com/kiwicom/schemathesis/issues>`_
and:

* Write a simple and descriptive title to identify your suggestion.
* Provide as many details as possible, explain your context and how the feature should work.
* Explain why this improvement would be useful.
* Keep the scope narrow. This will make it easier to implement.

.. _reportbugs:

Report bugs
-----------

Report bugs for Schemathesis in the `issue tracker <https://github.com/kiwicom/schemathesis/issues>`_.

If you are reporting a bug, please:

* Write a simple and descriptive title to identify the problem.
* Describe the exact steps which reproduce the problem in as many details as possible.
* Describe the behavior you observed after following the steps and point out what exactly is the problem with that behavior.
* Explain which behavior you expected to see instead and why.
* Include Python / Schemathesis versions.

It would be awesome if you can submit a failing test that demonstrates the problem.

.. _fixbugs:

Submitting Pull Requests
-----------------------

#. Fork the repository.
#. Enable and install `pre-commit <https://pre-commit.com>`_ to ensure style-guides and code checks are followed.
#. Target the ``master`` branch.
#. Follow **PEP-8** for naming and `black <https://github.com/psf/black>`_ for formatting.
#. Tests are run using ``tox``::

    tox -e pylint,mypy,py37

   The test environments above are usually enough to cover most cases locally.

#. Write an entry to `changelog.rst <https://github.com/kiwicom/schemathesis/blob/master/docs/changelog.rst>`_
#. Format your commit message according to the Conventional Commits `specification <https://www.conventionalcommits.org/en/>`_

For each pull request, we aim to review it as soon as possible.
If you wait a few days without a reply, please feel free to ping the thread by adding a new comment.

At present the core developers are:

- Dmitry Dygalo (`@Stranger6667`_)

Thanks!

.. _@Stranger6667: https://github.com/Stranger6667
