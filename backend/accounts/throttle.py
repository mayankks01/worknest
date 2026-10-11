import math

from django.conf import settings
from django.utils.crypto import salted_hmac
from redis import Redis

# INCR and expiry are atomic across web workers; both dimensions increment together.
SCRIPT = """
local counts = {}
for i, key in ipairs(KEYS) do
  local n = redis.call('INCR', key)
  if n == 1 then redis.call('EXPIRE', key, ARGV[1]) end
  counts[i] = n
end
local wait = 0
for i, key in ipairs(KEYS) do
  if counts[i] > tonumber(ARGV[i + 1]) then
    wait = math.max(wait, redis.call('TTL', key))
  end
end
return wait
"""


def check_rate(request, scope, email=""):
    ip_limit, email_limit, window = settings.AUTH_RATE_LIMITS[scope]
    # Do not trust client-supplied forwarding headers. Configure the reverse proxy
    # to preserve or securely establish REMOTE_ADDR before external deployment.
    values = [request.META.get("REMOTE_ADDR", "unknown")]
    limits = [ip_limit]
    if email:
        values.append(email)
        limits.append(email_limit)
    keys = [
        f"auth:{scope}:{index}:{salted_hmac('auth-rate', value).hexdigest()}"
        for index, value in enumerate(values)
    ]
    with Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2) as redis:
        return max(0, math.ceil(redis.eval(SCRIPT, len(keys), *keys, window, *limits)))
