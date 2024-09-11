import schemathesis


@schemathesis.check
def custom_check(ctx, response, case):
    raise AssertionError("\uc445")
