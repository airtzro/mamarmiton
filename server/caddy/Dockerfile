# Caddy with the DuckDNS plugin, so it can get a Let's Encrypt certificate
# through a DNS challenge: no port 80 needs to be opened on the router.
FROM caddy:2-builder AS builder
RUN xcaddy build --with github.com/caddy-dns/duckdns

FROM caddy:2
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
