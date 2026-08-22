from PIL import Image
import os

img = Image.open('static/logos/logo_p22_61.png')

# Login logo: up to 210px wide, 2x retina = 420px
w = 420
ratio = img.size[1] / img.size[0]
h = int(w * ratio)
img.resize((w, h), Image.LANCZOS).save('static/logos/logo.webp', 'WEBP', quality=92)
print('login logo:', w, 'x' + str(h), os.path.getsize('static/logos/logo.webp'), 'bytes')
