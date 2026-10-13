from django.conf import settings
from django.core.exceptions import RequestDataTooBig, TooManyFilesSent
from django.core.files.uploadhandler import FileUploadHandler, StopUpload
from django.http.multipartparser import MultiPartParserError

from accounts.views import error


class SizeLimit(FileUploadHandler):
    def __init__(self, request):
        super().__init__(request)
        self.received = 0

    def receive_data_chunk(self, raw_data, start):
        self.received += len(raw_data)
        if self.received > settings.ATTACHMENT_MAX_BYTES:
            self.request.upload_too_large = True
            raise StopUpload(connection_reset=False)
        return raw_data

    def file_complete(self, file_size):
        return None


class UploadLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.path.endswith("/attachments/"):
            try:
                if (
                    int(request.META.get("CONTENT_LENGTH", 0) or 0)
                    > settings.ATTACHMENT_MAX_BYTES + 65536
                ):
                    return error(
                        request, "FILE_TOO_LARGE", "The file exceeds the upload limit.", 413
                    )
            except ValueError:
                return error(request, "VALIDATION_ERROR", "Invalid request size.", 400)
            request.upload_handlers.insert(0, SizeLimit(request))
            # Parse here so malformed multipart bodies receive the same API envelope
            # even when CSRF middleware would otherwise trigger parsing first.
            try:
                _ = request.POST
            except RequestDataTooBig:
                return error(
                    request, "FILE_TOO_LARGE", "The request exceeds the upload limit.", 413
                )
            except (TooManyFilesSent, MultiPartParserError):
                return error(request, "VALIDATION_ERROR", "Upload exactly one valid file.", 400)
        return self.get_response(request)
