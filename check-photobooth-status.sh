#!/bin/bash

# Script to check photobooth status
# Usage: ./check-photobooth-status.sh

BASE_URL="http://localhost:3000"
ADMIN_EMAIL="admin@photoboth.com"
ADMIN_PASSWORD="Admin123!"

echo "🔐 Logging in..."
LOGIN_RESPONSE=$(curl -s -X POST "$BASE_URL/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASSWORD\"}")

TOKEN=$(echo $LOGIN_RESPONSE | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)

if [ -z "$TOKEN" ]; then
  echo "❌ Login failed!"
  echo "$LOGIN_RESPONSE"
  exit 1
fi

echo "✅ Login successful!"
echo ""

echo "📊 =========================================="
echo "   PHOTOBOOTH SYSTEM STATUS"
echo "=========================================="
echo ""

curl -s -X GET "$BASE_URL/api/v1/photobooth/status" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" | jq '.'

echo ""
echo "📋 =========================================="
echo "   AVAILABLE PHOTOBOOTHS"
echo "=========================================="
echo ""

AVAILABLE=$(curl -s -X GET "$BASE_URL/api/v1/photobooth/available" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json")

echo "$AVAILABLE" | jq '.'

COUNT=$(echo "$AVAILABLE" | jq '. | length')
echo ""
echo "📈 Total available: $COUNT"




