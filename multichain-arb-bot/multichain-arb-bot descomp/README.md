# Multi-Chain Flash Loan Arbitrage Bot

Bot de arbitraje multi-par con flash loans para **Arbitrum** y **Base**, adaptado a partir del bot original de arbitraje MAGIC/USDC en Arbitrum.

## Arquitectura

```
┌─────────────────────────────────────────────────────┐
│                    Bot (Python)                      │
│  ┌───────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  Scanner   │  │ Price Engine │  │  TX Executor │  │
│  │ (main.py)  │→ │  (quotes)   │→ │ (flash arb)  │  │
│  └───────────┘  └──────────────┘  └──────────────┘  │
└──────────┬──────────────┬──────────────┬────────────┘
           │              │              │
    ┌──────▼──────┐ ┌─────▼─────┐ ┌─────▼──────┐
    │  Arbitrum   │ │   Base    │ │  Aave V3   │
    │ Uni/Sushi/  │ │ Uni/Sushi/│ │ Flash Loan │
    │  Camelot    │ │ Aerodrome │ │  Provider  │
    └─────────────┘ └───────────┘ └────────────┘
```

## Características

- **Multi-chain**: Opera en Arbitrum y Base simultáneamente
- **Multi-par**: Monitorea 13+ pares en Arbitrum, 10+ en Base
- **Multi-DEX**: Uniswap V3, SushiSwap, Camelot (Arbitrum), Aerodrome (Base)
- **Flash Loans**: Aave V3 flash loans para operaciones sin capital inicial
- **Modo directo**: También soporta arbitraje con balance propio del contrato
- **Dry Run**: Modo simulación para testeo sin riesgo
- **Configurable**: Trade sizes, slippage, gas, pares y cadenas vía `.env`

## Estructura del Proyecto

```
multichain-arb-bot/
├── contracts/
│   └── FlashArbExecutor.sol    # Smart contract (Arbitrum & Base)
├── config/
│   ├── __init__.py
│   └── chains.py               # Direcciones de tokens, DEXes, pares
├── bot/
│   ├── __init__.py
│   ├── main.py                 # Loop principal del bot
│   ├── price_engine.py         # Motor de precios multi-DEX
│   └── executor.py             # Constructor y sender de transacciones
├── .env.example
├── requirements.txt
└── README.md
```

## Setup

### 1. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 2. Configurar `.env`

```bash
cp .env.example .env
# Editar .env con tus claves y RPC endpoints
```

### 3. Deployar el contrato

Deployar `FlashArbExecutor.sol` en cada cadena usando Remix o Hardhat:

**Constructor args para Arbitrum:**
```
_aaveProvider: 0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb
_routers: [
  "0xE592427A0AEce92De3Edee1F18E0157C05861564",  // Uniswap V3 (id=0)
  "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",  // SushiSwap  (id=1)
  "0xc873fEcbd354f5A56E00E710B90EF4201db2448d"   // Camelot    (id=2)
]
```

**Constructor args para Base:**
```
_aaveProvider: 0xe20fCBdBfFC4Dd138cE8b2E6FBb6CB49777ad64D
_routers: [
  "0x2626664c2603336E57B271c5C0b26F421741e481",  // Uniswap V3  (id=0)
  "0x6BDED42c6DA8FBf0d2bA55B2fa120C5e0c8D7891",  // SushiSwap   (id=1)
  "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43"   // Aerodrome   (id=2)
]
```

### 4. Ejecutar el bot

```bash
# Modo dry run (simulación)
DRY_RUN=true python -m bot.main

# Modo live
DRY_RUN=false python -m bot.main
```

## Flujo de Operación

1. **Scan**: El bot consulta precios en todos los DEXes para cada par configurado
2. **Detect**: Compara precios cruzados y calcula profit neto (restando gas + premium del flash loan)
3. **Select**: Elige la mejor oportunidad entre todas las cadenas
4. **Execute**: Envía una transacción al contrato `FlashArbExecutor`:
   - Pide flash loan de Aave V3
   - Swap en DEX A (compra barato)
   - Swap en DEX B (vende caro)
   - Repaga flash loan + premium (0.05%)
   - Profit se envía al owner

## Cambios vs Bot Original

| Aspecto | Bot Original | Bot Nuevo |
|---------|-------------|-----------|
| Cadenas | Solo Arbitrum | Arbitrum + Base |
| Pares | MAGIC/USDC únicamente | 23+ pares configurables |
| Capital | Requiere balance previo | Flash loans de Aave V3 |
| DEXes | Uniswap V3 + SushiSwap | + Camelot + Aerodrome |
| Contrato | Par fijo hardcodeado | Routing dinámico multi-step |
| Ejecución | Funciones separadas por dirección | Struct genérico de swap steps |

## Seguridad

- El contrato tiene `onlyOwner` y `nonReentrant`
- El flash loan callback verifica `initiator == address(this)` y `msg.sender == pool`
- Nunca compartas tu `PRIVATE_KEY`
- Usá `DRY_RUN=true` antes de operar en producción
- Monitoreá los logs y ajustá `MIN_PROFIT_PCT` según las condiciones del mercado

## Agregar Nuevos Pares o Cadenas

Para agregar un nuevo par, editá `config/chains.py`:

```python
# En la config de la cadena deseada:
pairs=[
    ...
    ("TOKEN_A", "TOKEN_B"),  # Agregar el par
],
tokens={
    ...
    "TOKEN_A": "0x...",  # Agregar la dirección
},
decimals={
    ...
    "TOKEN_A": 18,       # Agregar los decimales
},
```

Para agregar una nueva cadena, creá un nuevo `ChainConfig` en `chains.py` y agregalo al dict `CHAINS`.
