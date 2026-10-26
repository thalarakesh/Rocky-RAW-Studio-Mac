"""Rocky RAW Studio - local-only Windows desktop browser application."""
from __future__ import annotations
import csv, io, json, os, sys, threading, queue, webbrowser, socket, time, subprocess
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs
from collections import defaultdict
import shutil

RAW_EXT={'.arw','.cr2','.cr3','.nef','.nrw','.raf','.dng','.rw2','.orf','.pef','.srw','.3fr','.iiq','.k25','.kdc','.mos','.mrw','.rwl','.erf','.raw','.bay','.cap','.data','.eip','.fff','.mef','.mdc','.obm','.qtk','.rdc','.sti'}
JPEG_EXT={'.jpg','.jpeg'}
BASE=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
state={'source':'','client':'','dest':'','raw':[],'jpeg':[],'selected':set(),'missing':[],'copied':0,'failed':[],'busy':False,'processed':0,'total_copy':0,'copy_log':[]}
lock=threading.RLock()
dialog_requests=queue.Queue()

def scan(path,exts):
    base=Path(path); out=[]
    for root,dirs,files in os.walk(base):
        dirs.sort(key=str.casefold)
        for name in sorted(files,key=str.casefold):
            p=Path(root)/name
            if p.suffix.lower() in exts:
                out.append({'name':name,'stem':p.stem.casefold(),'relative':p.relative_to(base).as_posix(),'path':str(p)})
    return out

def public_state():
    with lock:
        return {'source':state['source'],'client':state['client'],'dest':state['dest'],
          'raw':[{k:v for k,v in f.items() if k!='path'} | {'id':i} for i,f in enumerate(state['raw'])],
          'clientCount':len(state['jpeg']),'selected':sorted(state['selected']), 'missing':list(state['missing']),
          'copied':state['copied'],'failed':state['failed'],'busy':state['busy'],
          'processed':state['processed'],'totalCopy':state['total_copy'], 'copyLog':state['copy_log'][-200:]}

def number_key(v):
    import re
    m=re.search(r'(\d+)$',v)
    return m.group(1).lstrip('0') or '0' if m else None

def match(mode, pasted):
    import re
    with lock:
        raw=state['raw']
        requested=(re.split(r'[\s,;]+',pasted) if mode=='numbers' else [f['name'] for f in state['jpeg']])
        requested=list(dict.fromkeys(Path(x.strip().replace('\\','/')).stem.casefold() for x in requested if x.strip()))
        byname=defaultdict(list); bynum=defaultdict(list)
        for i,f in enumerate(raw):
            byname[f['stem']].append(i)
            num=number_key(f['stem'])
            if num is not None: bynum[num].append(i)
        selected=set(); missing=[]
        for token in requested:
            hits=byname.get(token,[])
            if not hits and mode=='numbers':
                num=number_key(token)
                if num is not None and (token.isdigit() or re.fullmatch(r'0*\d+',token)):
                    hits=bynum.get(num,[])
            if len(hits)==1: selected.add(hits[0])
            elif not hits: missing.append(f'{token}: NOT FOUND')
            else: missing.append(f'{token}: AMBIGUOUS ({len(hits)} matches; choose manually)')
        state['selected']=selected;state['missing']=missing
        return {'matched':len(selected),'requested':len(requested),'missing':missing}

def copy_worker(indices):
    with lock:
        raws=list(state['raw']); dest=Path(state['dest']); source=Path(state['source'])
    for i in indices:
        f=raws[i]
        try:
            src=Path(f['path']); rel=Path(f['relative']); target=dest/rel
            if src.resolve()==target.resolve(): raise ValueError('Source and destination are the same file')
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():
                base=target.stem; ext=target.suffix; n=1
                while target.exists():
                    target=target.with_name(f'{base}_copy{n}{ext}');n+=1
            shutil.copy2(src,target)
            with lock:state['copied']+=1;state['copy_log'].append(f'COPIED: {rel} → {target.relative_to(dest)}')
        except Exception as e:
            with lock:state['failed'].append(f"{f['relative']}: {e}");state['copy_log'].append(f"FAILED: {f['relative']}: {e}")
        finally:
            with lock:state['processed']+=1
    with lock:state['busy']=False

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send_data(self,data,mime='application/json',status=200):
        body=json.dumps(data).encode() if isinstance(data,(dict,list)) else data
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_GET(self):
        p=urlsplit(self.path); path=p.path
        if path in ('/','/index.html'):
            self.send_data((BASE/'index.html').read_bytes(),'text/html; charset=utf-8')
        elif path=='/assets/logo.png':
            self.send_data((BASE/'assets/logo.png').read_bytes(),'image/png')
        elif path=='/api/state':self.send_data(public_state())
        elif path=='/api/thumbnail':
            try:
                index=int(parse_qs(p.query).get('id',['-1'])[0]); data=thumbnail(index)
                if data:self.send_data(data,'image/jpeg')
                else:self.send_data(b'', 'image/jpeg',404)
            except Exception:self.send_data(b'', 'image/jpeg',404)
        elif path=='/api/report':
            with lock:
                output=io.StringIO();w=csv.writer(output);w.writerow(['Type','Filename','Relative Path','Status'])
                for i,f in enumerate(state['raw']):w.writerow(['RAW',f['name'],f['relative'],'Selected' if i in state['selected'] else 'Not selected'])
                for msg in state['missing']:w.writerow(['Unmatched',msg,'','Missing or ambiguous'])
                for msg in state['copy_log']:w.writerow(['Copy',msg,'',''])
            content=output.getvalue().encode('utf-8-sig')
            self.send_response(200);self.send_header('Content-Type','text/csv; charset=utf-8');self.send_header('Content-Disposition','attachment; filename="Rocky_RAW_Report.csv"');self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
        else:self.send_data({'error':'Not found'},status=404)
    def do_POST(self):
        if self.headers.get('Content-Type','').split(';')[0]!='application/json' or self.headers.get('X-Rocky-App')!='1':
            self.send_data({'error':'Invalid request'},status=403);return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size>5_000_000:raise ValueError('Request too large')
            data=json.loads(self.rfile.read(size) or b'{}');path=urlsplit(self.path).path
            if path=='/api/choose':
                if data.get('kind') not in ('source','client','dest'):raise ValueError('Unknown folder kind')
                result=queue.Queue(maxsize=1);dialog_requests.put((data['kind'],result));folder=result.get(timeout=120)
                if folder:
                    with lock:
                        if state['busy']:raise ValueError('Copy in progress')
                        state[data['kind']]=folder
                        if data['kind']=='source':state['raw']=scan(folder,RAW_EXT);state['selected']=set();state['missing']=[];state['copied']=0;state['copy_log']=[]
                        if data['kind']=='client':state['jpeg']=scan(folder,JPEG_EXT)
                self.send_data(public_state())
            elif path=='/api/match':self.send_data(match(data.get('mode','numbers'),str(data.get('text',''))))
            elif path=='/api/select':
                with lock:
                    if state['busy']:raise ValueError('Copy in progress')
                    vals=data.get('ids',[])
                    if not isinstance(vals,list) or any(type(i)!=int or i<0 or i>=len(state['raw']) for i in vals):raise ValueError('Invalid selection')
                    state['selected']=set(vals)
                self.send_data(public_state())
            elif path=='/api/copy':
                with lock:
                    if state['busy']:raise ValueError('Copy already running')
                    if not state['dest'] or not state['source']:raise ValueError('Choose source and destination folders')
                    if not state['selected']:raise ValueError('Select photos first')
                    if Path(state['source']).resolve()==Path(state['dest']).resolve():raise ValueError('Destination must differ from source')
                    indices=sorted(state['selected']);state['busy']=True;state['copied']=0;state['failed']=[];state['processed']=0;state['total_copy']=len(indices);state['copy_log']=[]
                threading.Thread(target=copy_worker,args=(indices,),daemon=True).start();self.send_data({'ok':True})
            else:self.send_data({'error':'Not found'},status=404)
        except Exception as e:self.send_data({'error':str(e)},status=400)

def thumbnail(index):
    """JPEG sidecar or client JPEG by stem; optional rawpy for true RAW embedded previews."""
    try:
        with lock:
            if index<0 or index>=len(state['raw']):return None
            f=state['raw'][index];client=list(state['jpeg'])
        from PIL import Image,ImageOps
        source=Path(f['path'])
        candidates=[source.with_suffix(x) for x in ('.jpg','.JPG','.jpeg','.JPEG')]
        candidates += [Path(x['path']) for x in client if x['stem']==f['stem']]
        for p in candidates:
            if p.is_file():
                with Image.open(p) as im:
                    im=ImageOps.exif_transpose(im);im.thumbnail((350,260));buf=io.BytesIO();im.convert('RGB').save(buf,format='JPEG',quality=78);return buf.getvalue()
        try:
            import rawpy
            with rawpy.imread(str(source)) as r:
                try:thumb=r.extract_thumb()
                except Exception:return None
                if thumb.format==rawpy.ThumbFormat.JPEG:
                    with Image.open(io.BytesIO(thumb.data)) as im:
                        im.thumbnail((350,260));b=io.BytesIO();im.convert('RGB').save(b,'JPEG',quality=75);return b.getvalue()
                else:
                    im=Image.fromarray(thumb.data);im.thumbnail((350,260));b=io.BytesIO();im.convert('RGB').save(b,'JPEG',quality=75);return b.getvalue()
        except (ImportError,Exception):return None
    except Exception:return None


def main():
    """Native desktop window using pywebview; Tk dialogs run on their own UI thread."""
    import tkinter as tk
    from tkinter import filedialog

    ready=threading.Event()
    def dialog_thread():
        root=tk.Tk()
        root.withdraw()
        def poll_dialog():
            try:
                while True:
                    kind,result=dialog_requests.get_nowait()
                    try:
                        path=filedialog.askdirectory(
                            parent=root,
                            title={'source':'Choose RAW source folder',
                                   'client':'Choose client JPEG folder',
                                   'dest':'Choose destination folder'}[kind])
                        result.put(path or '')
                    except Exception:
                        result.put('')
            except queue.Empty:
                pass
            root.after(80,poll_dialog)
        root.after(80,poll_dialog)
        ready.set()
        root.mainloop()

    threading.Thread(target=dialog_thread,daemon=True).start()
    if not ready.wait(10):
        raise RuntimeError('Windows folder picker could not start')

    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    port=server.server_address[1]
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url=f'http://127.0.0.1:{port}/'
    try:
        import webview
        window=webview.create_window(
            'Rocky RAW Selection Studio',
            url,
            width=1360,
            height=900,
            min_size=(900,650),
            background_color='#0c1220',
            text_select=True)
        webview.start(debug=False)
    finally:
        server.shutdown()
        server.server_close()

if __name__=='__main__':
    main()
