import grpc
from sqlalchemy.exc import SQLAlchemyError

from common.logs import log


class FlightError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)


class ErrorInterceptor(grpc.ServerInterceptor):
    def intercept_service(self, continuation, details):
        handler = continuation(details)
        if handler is None:
            return None

        def call(request, context):
            try:
                return handler.unary_unary(request, context)
            except FlightError as error:
                context.abort(error.code, error.message)
            except SQLAlchemyError as error:
                log("database_error", method=details.method, error=type(error).__name__)
                context.abort(grpc.StatusCode.UNAVAILABLE, "Flight database is unavailable")

        return grpc.unary_unary_rpc_method_handler(
            call, request_deserializer=handler.request_deserializer, response_serializer=handler.response_serializer
        )
