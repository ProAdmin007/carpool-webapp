import os

from waitress import serve

from app import app

if __name__ == '__main__':
    options = {}
    if os.environ.get('TRUST_PROXY') == '1':
        # Achter een reverse proxy: neem https en het echte IP-adres over uit de proxy-headers.
        # TRUSTED_PROXY: IP-adres van de reverse proxy (aanbevolen); standaard elke afzender.
        options.update(trusted_proxy=os.environ.get('TRUSTED_PROXY', '*'), trusted_proxy_count=1,
                       trusted_proxy_headers='x-forwarded-for x-forwarded-proto x-forwarded-host',
                       clear_untrusted_proxy_headers=True)
    serve(app, host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', '5000')),
          threads=4, **options)
