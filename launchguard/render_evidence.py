from pathlib import Path
from pypdf import PdfReader
root=Path(__file__).resolve().parent.parent/'customer-data'/'soony-data'
out=root.parent.parent/'launchguard'/'customer-data'/'previews'
out.mkdir(exist_ok=True)
for file in list(root.glob('suppliers/*/contracts/*.pdf'))+list(root.glob('suppliers/*/order_confirmations/PL_*.pdf')):
    doc=PdfReader(file)
    if len(''.join(page.extract_text() or '' for page in doc.pages).strip())>=20:
        continue
    for index,page in enumerate(doc.pages):
        for image in page.images:
            dest=out/(file.stem+'-'+str(index+1)+'-'+image.name)
            dest.write_bytes(image.data)
            print(dest.name)
