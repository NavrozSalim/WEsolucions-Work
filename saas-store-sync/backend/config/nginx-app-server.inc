# Included inside both :80 and :443 server blocks (paths are container paths).
client_max_body_size 10M;

location ^~ /api/ {
    proxy_pass http://django_backend;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_connect_timeout 120s;
    proxy_read_timeout 120s;
    proxy_send_timeout 120s;
}

location ^~ /admin/ {
    proxy_pass http://django_backend;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location ^~ /static/ {
    proxy_pass http://django_backend;
    proxy_set_header Host $host;
}

location ^~ /health/ {
    proxy_pass http://django_backend;
    proxy_set_header Host $host;
}

location ^~ /ready/ {
    proxy_pass http://django_backend;
    proxy_set_header Host $host;
}

location ^~ /metrics/ {
    proxy_pass http://django_backend;
    proxy_set_header Host $host;
}

location / {
    root /usr/share/nginx/html;
    try_files $uri $uri/ /index.html;
}
