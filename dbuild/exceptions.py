class DbuildException(Exception):
    pass


class DbuildBuildFailedException(DbuildException):
    pass


class DbuildSourceBuildFailedException(DbuildException):
    pass


class DbuildBinaryBuildFailedException(DbuildException):
    pass
