import threading
import queue
import webview
import app


def main():
    server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    window = webview.create_window(
        "Rocky RAW Studio",
        f"http://127.0.0.1:{server.server_address[1]}/",
        width=1280,
        height=850,
        min_size=(900, 600),
    )

    def handle_folder_dialogs():
        while True:
            try:
                kind, result = app.dialog_requests.get(timeout=0.2)
            except queue.Empty:
                continue

            try:
                folders = window.create_file_dialog(webview.FileDialog.FOLDER)
                result.put(folders[0] if folders else "")
            except Exception:
                result.put("")

    try:
        webview.start(handle_folder_dialogs, gui="cocoa")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
