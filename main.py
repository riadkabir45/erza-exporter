import argparse
import importlib
import pkgutil
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
import yaml
import modules

# Global storage for thread-safe module data cache and static headers
_module_headers = {}
_module_data_cache = {}
_cache_locks = {}

class MetricsHandler(BaseHTTPRequestHandler):
    loaded_modules = []

    def do_GET(self):
        if self.path == '/metrics':
            try:
                metrics_output = self.collect_all_metrics()
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8')
                self.end_headers()
                self.wfile.write(metrics_output.encode('utf-8'))
            except (BrokenPipeError, ConnectionResetError):
                pass
            except OSError as e:
                # Catch low-level socket closure errors (Broken pipe / Connection reset)
                if e.errno in (32, 104):
                    pass
                else:
                    raise
            except Exception as e:
                try:
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(f"# Error collecting metrics: {e}\n".encode('utf-8'))
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
        else:
            try:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not Found. Try /metrics\n")
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    def collect_all_metrics(self):
        all_lines = []
        
        # 1. Output all static HELP and TYPE headers first (guaranteed never missing)
        for name, headers in _module_headers.items():
            all_lines.extend(headers)

        # 2. Output dynamic metric value rows from cache or live collect
        for name, collect_func, is_cached in self.loaded_modules:
            try:
                if is_cached:
                    with _cache_locks[name]:
                        data_rows = _module_data_cache.get(name, [])
                else:
                    data_rows = collect_func()
                
                if isinstance(data_rows, list):
                    all_lines.extend(data_rows)
            except Exception as e:
                all_lines.append(f"# Error collecting data from module {name}: {e}")
                
        return "\n".join(all_lines) + "\n"

    def log_message(self, format, *args):
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
            
            # Enforce module contract: must have meta() and collect()
            if not (hasattr(mod, 'meta') and callable(mod.meta)):
                print(f"Skipping module {module_name}: missing callable 'meta()' function")
                continue
            if not (hasattr(mod, 'collect') and callable(mod.collect)):
                print(f"Skipping module {module_name}: missing callable 'collect()' function")
                continue

            # 1. Fetch metadata (headers, pre_scrape config)
            metadata = mod.meta()
            headers = metadata.get('headers', [])
            _module_headers[module_name] = headers

            pre_scrape_conf = metadata.get('pre_scrape', {})
            is_enabled = pre_scrape_conf.get('enabled', False)
            
            if is_enabled:
                interval = pre_scrape_conf.get('interval_seconds', 300)
                print(f"Loaded module '{module_name}' [Background Caching Enabled: {interval}s interval]")
                
                _cache_locks[module_name] = threading.Lock()
                _module_data_cache[module_name] = [] # Starts empty until background worker populates it

                # Background worker thread
                def background_worker(m_name, collect_func, intvl):
                    while True:
                        try:
                            data_rows = collect_func()
                            if isinstance(data_rows, list):
                                with _cache_locks[m_name]:
                                    _module_data_cache[m_name] = data_rows
                                print(f"Background cache updated successfully for module: {m_name}")
                        except Exception as e:
                            print(f"Background worker error in module {m_name}: {e} (retaining stale cache)")
                        
                        time.sleep(intvl)

                # Run first collection immediately in background thread
                t = threading.Thread(
                    target=background_worker, 
                    args=(module_name, mod.collect, interval), 
                    daemon=True
                )
                t.start()

                discovered.append((module_name, mod.collect, True))
            else:
                print(f"Loaded module '{module_name}' [Synchronous on-demand scraping]")
                discovered.append((module_name, mod.collect, False))

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

    config = load_config(args.config)
    server_config = config.get('server', {})
    host = server_config.get('host', '0.0.0.0')
    port = server_config.get('port', 9100)

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
