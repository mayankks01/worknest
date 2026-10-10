from uuid import uuid4


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Generate locally: never reflect an untrusted header into logs or responses.
        request.request_id = str(uuid4())
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        response["Cache-Control"] = "no-store"
        return response
