import threading
import webview
import app


def main():
    server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    webview.create_window(
        "Rocky RAW Studio",
        f"http://127.0.0.1:{server.server_address[1]}/",
        width=1280,
        height=850,
        min_size=(900, 600),
    )

    try:
        webview.start(gui="cocoa")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
