FROM nginx:1.29-alpine

RUN apk add --no-cache apache2-utils

COPY infra/supabase/studio-nginx.conf /etc/nginx/conf.d/default.conf
COPY infra/supabase/studio-gateway-entrypoint.sh /opt/prismux/studio-gateway-entrypoint.sh

ENTRYPOINT ["/bin/sh", "/opt/prismux/studio-gateway-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
