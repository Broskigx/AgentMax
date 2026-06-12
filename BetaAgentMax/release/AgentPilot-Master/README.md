# NixControl Master (Beta v2.0)

## Como usar

1. Doble-click en NixControl-Master.bat
2. La primera vez tarda 5-15 min (instala dependencias).
3. Se abre la UI. En la pestana Coordinador:
   - Configura el puerto (12356 por defecto)
   - Click en Iniciar
4. Tu IP aparece en la consola del launcher. Compartela con tus amigos.
5. Ellos ejecutan NixControl-Contributor.bat y ponen tu IP.

## Tabs

- Coordinador: Server federado FedAvg (tus amigos se conectan aqui)
- Hardware: Escaneo + benchmark + config optima automatica
- Dispositivos: Tabla en vivo de quien esta conectado y entrenando
- DDP: Modo clasico con 1 amigo via ZeroTier
- Config: Modelo base, output dir, bug collector

## Requisitos

- Windows 10/11
- Python 3.10-3.12 (instalado y en PATH)
- GPU NVIDIA con CUDA 12.1+ (recomendado)
- ~30 GB de disco para modelo + dataset
