class DbuildException(Exception):
    pass


class DbuildDockerBuildFailedException(DbuildException):
        pass


class DbuildBuildFailedException(DbuildException):
    pass


class DbuildSourceBuildFailedException(DbuildException):
    pass


class DbuildBinaryBuildFailedException(DbuildException):
    pass
