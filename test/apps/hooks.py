import schemathesis


@schemathesis.check
def custom_check(response, case):
    raise AssertionError("\uc445")
