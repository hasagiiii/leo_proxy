FROM nginx:1.27-alpine
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY nginx/40-runtime-config.sh /docker-entrypoint.d/40-runtime-config.sh
COPY dist /usr/share/nginx/html
RUN chmod +x /docker-entrypoint.d/40-runtime-config.sh

EXPOSE 80
