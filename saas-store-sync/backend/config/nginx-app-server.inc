# Included inside both :80 and :443 server blocks (paths are container paths).
client_max_body_size 10M;

location ^~ /api/ {
    proxy_pass http://django_backend;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering on;
    proxy_buffer_size 16k;
    proxy_buffers 8 16k;
    proxy_busy_buffers_size 32k;
    proxy_connect_timeout 300s;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
}

location ^~ /admin/ {
    proxy_pass http://django_backend;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering on;
    proxy_buffer_size 16k;
    proxy_buffers 8 16k;
    proxy_busy_buffers_size 32k;
}

location ^~ /static/ {
    proxy_pass http://django_backend;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
}

# Listing photos (and other MEDIA) — must reach Django before SPA ``location /``.
location ^~ /media/ {
    proxy_pass http://django_backend;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location ^~ /health/ {
    proxy_pass http://django_backend;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_buffering on;
    proxy_buffer_size 8k;
    proxy_buffers 4 8k;
}

location ^~ /ready/ {
    proxy_pass http://django_backend;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_buffering on;
    proxy_buffer_size 8k;
    proxy_buffers 4 8k;
}

location ^~ /metrics/ {
    proxy_pass http://django_backend;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_buffering on;
    proxy_buffer_size 16k;
    proxy_buffers 4 16k;
}

location / {
    root /usr/share/nginx/html;
    try_files $uri $uri/ /index.html;
}
