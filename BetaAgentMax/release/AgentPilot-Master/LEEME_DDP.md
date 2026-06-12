# 🚀 NixControl DDP - Entrenamiento en 2 PCs

## 📦 Requisitos (ambos PCs)

1. **ZeroTier** (LAN virtual): https://www.zerotier.com/download/
2. **Python 3.12** con el mismo `.venv` (o instala las mismas dependencias)
3. **GPU NVIDIA** (ambos)

## 🔧 Setup en 5 minutos

### 1. Instalar ZeroTier (ambos)
- Descargar e instalar ZeroTier desde https://www.zerotier.com/download/
- Unirse a la misma red (crea una o usa una existente)
- **Tú (master)**: creas la red en https://my.zerotier.com/ y autorizas los dos dispositivos
- Anota las IP virtuales de cada PC (ej: 192.168.196.1 y 192.168.196.2)

### 2. Copiar el proyecto (ambos)
- Copia toda la carpeta `training/` a la misma ruta en el otro PC
- O mejor: comparte por Google Drive / USB / GitHub

### 3. Configurar IPs
- **Tú (master)**: abre `run_master.bat` y pon tu IP virtual en `MASTER_ADDR`
- **Tu amigo (worker)**: abre `run_worker.bat` y pon TU IP (la del master) en `MASTER_ADDR`

### 4. Probar conexión (worker)
```powershell
Test-NetConnection IP_DEL_MASTER -Port 12355
```
Debe dar `TcpTestSucceeded: True`

## ▶️ Ejecutar

**1º Tú (master)**: haz doble clic en `run_master.bat`
**2º Tu amigo**: 30 segundos después, haz clic en `run_worker.bat`

El master mostrará los logs de entrenamiento. Ambos verán el loss.

## 📤 Después del entrenamiento

Tú (master) tendrás los adapters guardados en `outputs/NixControl-7b-v1.0/adapter/`.
El worker no guarda nada (solo ayuda a entrenar).

## ⚡ ¿Cuánto más rápido?

| PCs | Tiempo estimado |
|-----|----------------|
| 1   | ~6 horas |
| 2   | ~3-4 horas |
| 3   | ~2 horas |
| 4   | ~1.5 horas |

## ❓ Problemas comunes

- **"No se pueden conectar"**: firewall, desactiva firewall de Windows o añade regla para puerto 12355
- **"Timeout"**: ZeroTier no está autorizado, revisa https://my.zerotier.com/
- **"Different cuda devices"**: ambas GPUs deben ser compatibles (RTX 4060 Ti funciona)
