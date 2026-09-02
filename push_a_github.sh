#!/bin/bash

# Script para crear repo en GitHub y hacer push automático

USER="tuchamaco147"
REPO="carpeta-railway"

echo "🚀 Creando repositorio en GitHub..."
echo ""

# Opción 1: Usar GitHub CLI
if command -v gh &> /dev/null; then
    echo "Usando GitHub CLI..."
    gh repo create $REPO --public --source=. --remote=origin --push

    if [ $? -eq 0 ]; then
        echo ""
        echo "✅ ¡Repo creado y código pusheado!"
        echo ""
        echo "URL: https://github.com/$USER/$REPO"
        exit 0
    fi
fi

# Opción 2: Crear repo manualmente en web, luego push
echo "⚠️  No se pudo usar GitHub CLI"
echo ""
echo "Haz esto manualmente:"
echo ""
echo "1. Ve a: https://github.com/new"
echo "2. Nombre: $REPO"
echo "3. Click 'Create repository'"
echo ""
echo "4. Luego en tu terminal, copia y pega:"
echo ""
echo "git remote add origin https://github.com/$USER/$REPO.git"
echo "git branch -M main"
echo "git push -u origin main"
echo ""
