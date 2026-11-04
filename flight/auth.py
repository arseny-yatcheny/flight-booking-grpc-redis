import hmac

import grpc


class ApiKeyInterceptor(grpc.ServerInterceptor):
    def __init__(self, api_key):
        if not api_key:
            raise ValueError("GRPC_API_KEY is required")
        self.api_key = api_key.encode()

    def intercept_service(self, continuation, details):
        handler = continuation(details)
        if handler is None:
            return None
        supplied = [value for key, value in details.invocation_metadata if key == "x-api-key"]
        if len(supplied) == 1 and hmac.compare_digest(supplied[0].encode(), self.api_key):
            return handler

        def reject(request, context):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Missing or invalid service API key")

        return grpc.unary_unary_rpc_method_handler(
            reject, request_deserializer=handler.request_deserializer, response_serializer=handler.response_serializer
        )
