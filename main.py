import argparse
import importlib
import pkgutil
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
import yaml
import modules

class MetricsHandler(BaseHTTPRequestHandler):
    # Class-level reference populated during server startup
    loaded_modules = []

    def do_GET(self):
        if self.path == '/metrics':
            try:
                metrics_output = self.collect_all_metrics()
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8')
                self.end_headers()
                self.wfile.write(metrics_output.encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f"# Error collecting metrics: {e}\n".encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found. Try /metrics\n")

    def collect_all_metrics(self):
        all_lines = []
        
        for name, func in self.loaded_modules:
            try:
                result = func()
                if isinstance(result, list):
                    all_lines.extend(result)
                elif isinstance(result, str):
                    all_lines.append(result)
            except Exception as e:
                all_lines.append(f"# Error in module {name}: {e}")
                
        return "\n".join(all_lines) + "\n"

    def log_message(self, format, *args):
        # Suppress default access logs to keep terminal clean
        return

def load_config(config_path):
    try:
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"Error loading config file '{config_path}': {e}")
        sys.exit(1)

def discover_modules():
    discovered = []
    package = modules
    for _, module_name, _ in pkgutil.iter_modules(package.__path__):
        try:
            mod = importlib.import_module(f"modules.{module_name}")
            if hasattr(mod, 'main') and callable(mod.main):
                discovered.append((module_name, mod.main))
                print(f"Loaded module: {module_name}")
            else:
                print(f"Skipping module {module_name}: missing callable 'main()' function")
        except Exception as e:
            print(f"Failed to load module {module_name}: {e}")
    return discovered

def run():
    parser = argparse.ArgumentParser(description="Modular Python Prometheus Exporter")
    parser.add_argument(
        '-c', '--config', 
        default='config.yaml', 
        help="Path to YAML configuration file (default: config.yaml)"
    )
    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)
    server_config = config.get('server', {})
    host = server_config.get('host', '0.0.0.0')
    port = server_config.get('port', 9100)

    # Discover modules once on start
    print("Discovering modules...")
    MetricsHandler.loaded_modules = discover_modules()

    server_address = (host, port)
    httpd = HTTPServer(server_address, MetricsHandler)
    print(f"Custom modular exporter listening on {host}:{port}...")
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down exporter...")
        httpd.server_close()

if __name__ == '__main__':
    run()
