# SiloEdit Restore

Aplicación de escritorio local para Windows que inspecciona imágenes por lotes e intenta recuperaciones conservadoras. Los archivos de origen nunca se modifican y las imágenes no se envían a servicios externos.

## Requisitos e instalación

- Windows 10/11 y Python 3.10 o posterior.
- Crear y activar un entorno virtual en PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Si PowerShell bloquea la activación, podés usar directamente el ejecutable del entorno sin activarlo:
`.\.venv\Scripts\python.exe -m pip install -r requirements.txt` y después
`.\.venv\Scripts\python.exe app.py`.

NOMOS 8k HAT-L y CodeFormer se ejecutan en un entorno Python 3.12 aislado con los checkpoints locales:

- `assets/test/4xNomos8kHAT-L_otf.pth`
- `assets/test/codeformer.pth`

```powershell
py -3.12 -m venv "$env:LOCALAPPDATA\PixelRestore\realesrgan-runtime"
& "$env:LOCALAPPDATA\PixelRestore\realesrgan-runtime\Scripts\python.exe" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
& "$env:LOCALAPPDATA\PixelRestore\realesrgan-runtime\Scripts\python.exe" -m pip install -r requirements-realesrgan.txt
```

Elegí la variante de PyTorch apropiada para el controlador y el hardware del equipo. Si falta el entorno, el checkpoint o una dependencia, la aplicación informa el error y no presenta una salida fallida como inferencia de IA. El procesamiento es local y usa tiling para limitar memoria.

CodeFormer usa los archivos de arquitectura oficiales v0.1.0 guardados localmente en `%LOCALAPPDATA%\PixelRestore\models\CodeFormer`. Para preparar esa carpeta una vez:

```powershell
$codeformerRoot = Join-Path $env:LOCALAPPDATA "PixelRestore\models\CodeFormer"
$codeformerArchs = Join-Path $codeformerRoot "basicsr\archs"
New-Item -ItemType Directory -Path $codeformerArchs -Force
curl.exe -L --fail "https://raw.githubusercontent.com/sczhou/CodeFormer/v0.1.0/basicsr/archs/codeformer_arch.py" -o (Join-Path $codeformerArchs "codeformer_arch.py")
curl.exe -L --fail "https://raw.githubusercontent.com/sczhou/CodeFormer/v0.1.0/basicsr/archs/vqgan_arch.py" -o (Join-Path $codeformerArchs "vqgan_arch.py")
curl.exe -L --fail "https://raw.githubusercontent.com/sczhou/CodeFormer/master/LICENSE" -o (Join-Path $codeformerRoot "LICENSE")
```

Los archivos `assets/test/detection_mobilenet0.25_Final.pth` y `assets/test/parsing_parsenet.pth` se usan localmente para detectar y componer las caras, sin descargar pesos durante el escaneo. CodeFormer se distribuye bajo la licencia S-Lab 1.0, que limita el uso comercial; revisar la licencia y obtener autorización antes de incluirlo en una distribución comercial.

Para publicar los checkpoints en GitHub se requiere Git LFS: los archivos de HAT y CodeFormer exceden el límite de 100 MB por archivo de GitHub. La regla está en `.gitattributes`; instalá y habilitá Git LFS antes de agregar los archivos. Revisá también las licencias antes de distribuir los pesos.

## Uso

1. En **Escanear**, seleccioná una carpeta de origen y otra de destino, fuera del árbol de origen.
2. Elegí si querés recorrer subcarpetas y qué extensiones incluir.
3. Iniciá el análisis. Se puede pausar entre archivos o cancelar el lote.
4. Revisá las salidas en **Resultados**, exportá el CSV o consultá el historial local. La vista previa prioriza la composición `_final`, luego la portada `_portada` y finalmente la recuperación parcial. El doble clic abre un visor integrado con zoom; desde allí también podés abrir la imagen con la aplicación predeterminada de Windows.

El archivo [examples/informe_ejemplo.csv](examples/informe_ejemplo.csv) muestra el formato de exportación con datos completamente ficticios; no es un informe de un análisis real.

`fotos_originales_rotas/` conserva una imagen de ejemplo para probar el flujo; la aplicación no altera los originales de origen.

Las imágenes válidas se informan, pero no se copian. Una imagen dañada puede producir una salida **Parcial** si Pillow logra decodificarla y la salida supera la validación. Si solo se genera una portada, Resultados la identifica como **Portada generada**. Los archivos sin salida validada se informan como no recuperables o con error. Las salidas existentes nunca se sobrescriben; se agrega un sufijo numérico.

## Flujo compacto para JPEG dañado

SiloEdit Restore inspecciona los marcadores JPEG (SOI, APP/EXIF, DQT, DHT, SOF, SOS y EOI), localiza el escaneo e informa la geometría MCU y anomalías estructurales detectables. Esto no decodifica coeficientes DCT ni permite afirmar que los bloques puedan repararse.

Cuando detecta anomalías binarias y encuentra una miniatura EXIF utilizable, conserva:

- `destino/_portada/<subcarpetas>/<nombre>_cover_nomos8khat_4x.png`: portada EXIF procesada una vez por NOMOS 8k HAT-L (4×).
- `destino/_final/<subcarpetas>/<nombre>_final.png`: composición final a la resolución decodificada de la imagen dañada, con detalle general de HAT y las caras de la salida CodeFormer.

La miniatura EXIF, la variante facial CodeFormer, la máscara, el híbrido base y los archivos auxiliares se crean temporalmente y se eliminan al terminar; solo se conservan `_portada` y `_final` para este flujo. La recuperación parcial, si se valida, permanece como salida independiente en la ruta relativa original dentro del destino. Los JPEG originales se mantienen intactos en la carpeta de origen. Las imágenes válidas sin anomalías no activan este flujo.

El flujo activo queda reducido a una sola ampliación general: NOMOS HAT procesa la miniatura EXIF en una inferencia 4×. CodeFormer (fidelidad 0.7) procesa las caras detectadas en esa portada HAT; no se ejecutan x2Plus, doble pasada ni GFPGAN. Durante cada análisis, ambos modelos se cargan una vez y se reutilizan; después de cada inferencia sus pesos vuelven a RAM para limitar el uso de VRAM. Esto evita recargar los checkpoints por cada foto sin cambiar los modelos, la fidelidad ni la composición. Si CodeFormer falla, HAT sigue disponible como guía general para la composición. Si HAT falla, CodeFormer no se ejecuta porque no hay una portada ampliada de entrada. Los errores quedan visibles. El borde de la máscara se difumina con un radio de `max(8 px, lado menor / 24)` para hacer más gradual la mezcla.

SiloEdit Restore estima una máscara de regiones dañadas y compone allí la salida CodeFormer, que conserva la portada HAT como base general y refina las caras detectadas; fuera de la máscara mantiene la imagen decodificada. Si CodeFormer no genera una salida válida, usa HAT como guía. La máscara es heurística y puede marcar zonas grises auténticas. Todo detalle inferido por HAT/CodeFormer puede diferir del original; el fundido no reconstruye información ausente ni garantiza una unión invisible. Las salidas permanentes de IA son `_portada` y `_final`; los originales de origen nunca se modifican.

## Alcance y limitaciones

La detección y composición se basan en heurísticas, no en reparación real MCU/DCT ni resincronización Huffman. SiloEdit Restore no inserta, elimina ni desplaza MCUs. La miniatura EXIF es de baja resolución; la superresolución puede estimar textura plausible, pero no recuperar datos ausentes ni garantizar la apariencia original.

Formatos incluidos: JPG/JPEG, PNG, WEBP, BMP, TIFF y GIF. El historial SQLite guarda metadatos y filas de resultados, no imágenes; se almacena en `%LOCALAPPDATA%\PixelRestore`.

## Pruebas

Con las dependencias instaladas, ejecutá `python -m pytest -q`. Las pruebas sintéticas comprueban el tratamiento de entradas dañadas, conservación de originales, uso de la portada como entrada del modelo, selección prioritaria de la composición final y prevención de sobrescrituras.

## Empaquetado para Windows

```powershell
python -m pip install pyinstaller
pyinstaller --noconfirm --windowed --name "SiloEdit Restore" --icon assets\icons\SiloEditRestore.ico app.py
```

Esto genera una compilación inicial de la interfaz en `dist\SiloEdit Restore`; por sí sola no incluye el runtime aislado de IA ni los modelos. Prepará y probá esos componentes antes de distribuir una instalación completa.

## Licencia

MIT; ver [LICENSE](LICENSE).
