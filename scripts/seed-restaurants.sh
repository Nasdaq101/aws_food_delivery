#!/bin/bash

# Script to seed the restaurants table with sample data

REGION=${AWS_REGION:-us-west-1}
TABLE_NAME="FoodDelivery-Restaurants"

echo "Seeding restaurants table with 20 restaurants..."

restaurants='[
  {"id":"rest_001","name":"The Spice Route","cuisine":"Indian","address":"245 Castro St, San Francisco, CA 94114","phone":"+1-415-555-1001","rating":"4.8","time":"25-35","img":"https://images.unsplash.com/photo-1585937421612-70a008356fbe?w=400"},
  {"id":"rest_002","name":"Noodle Nirvana","cuisine":"Thai","address":"1523 Fillmore St, San Francisco, CA 94115","phone":"+1-415-555-1002","rating":"4.6","time":"30-40","img":"https://images.unsplash.com/photo-1569718212165-3a8278d5f624?w=400"},
  {"id":"rest_003","name":"Mediterranean Breeze","cuisine":"Greek","address":"3458 Geary Blvd, San Francisco, CA 94118","phone":"+1-415-555-1003","rating":"4.5","time":"35-45","img":"https://images.unsplash.com/photo-1544025162-d76694265947?w=400"},
  {"id":"rest_004","name":"Seoul Kitchen","cuisine":"Korean","address":"789 Clement St, San Francisco, CA 94118","phone":"+1-415-555-1004","rating":"4.9","time":"20-30","img":"https://images.unsplash.com/photo-1580462447823-f3f8c1d6be3d?w=400"},
  {"id":"rest_005","name":"La Famiglia Trattoria","cuisine":"Italian","address":"2134 Chestnut St, San Francisco, CA 94123","phone":"+1-415-555-1005","rating":"4.7","time":"25-35","img":"https://images.unsplash.com/photo-1595295333158-4742f28fbd85?w=400"},
  {"id":"rest_006","name":"Dragon Palace","cuisine":"Chinese","address":"876 Grant Ave, San Francisco, CA 94108","phone":"+1-415-555-1006","rating":"4.4","time":"30-40","img":"https://images.unsplash.com/photo-1525755662778-989d0524087e?w=400"},
  {"id":"rest_007","name":"Pho Heaven","cuisine":"Vietnamese","address":"1456 Irving St, San Francisco, CA 94122","phone":"+1-415-555-1007","rating":"4.8","time":"20-30","img":"https://images.unsplash.com/photo-1555126634-323283e090fa?w=400"},
  {"id":"rest_008","name":"El Mariachi","cuisine":"Mexican","address":"567 Valencia St, San Francisco, CA 94110","phone":"+1-415-555-1008","rating":"4.3","time":"25-35","img":"https://images.unsplash.com/photo-1613514785940-daed07799d9b?w=400"},
  {"id":"rest_009","name":"Tokyo Express","cuisine":"Japanese","address":"2345 Lombard St, San Francisco, CA 94123","phone":"+1-415-555-1009","rating":"4.7","time":"15-25","img":"https://images.unsplash.com/photo-1553621042-f6e147245754?w=400"},
  {"id":"rest_010","name":"Le Petit Bistro","cuisine":"French","address":"987 Union St, San Francisco, CA 94133","phone":"+1-415-555-1010","rating":"4.9","time":"40-50","img":"https://images.unsplash.com/photo-1559339352-11d035aa65de?w=400"},
  {"id":"rest_011","name":"BBQ Masters","cuisine":"American","address":"3421 Divisadero St, San Francisco, CA 94123","phone":"+1-415-555-1011","rating":"4.6","time":"30-40","img":"https://images.unsplash.com/photo-1544025162-d76694265947?w=400"},
  {"id":"rest_012","name":"Tapas Barcelona","cuisine":"Spanish","address":"1678 Haight St, San Francisco, CA 94117","phone":"+1-415-555-1012","rating":"4.5","time":"25-35","img":"https://images.unsplash.com/photo-1595514535116-2d3b436b4696?w=400"},
  {"id":"rest_013","name":"Curry Express","cuisine":"Indian","address":"2890 Mission St, San Francisco, CA 94110","phone":"+1-415-555-1013","rating":"4.4","time":"20-30","img":"https://images.unsplash.com/photo-1574484284002-952d92456975?w=400"},
  {"id":"rest_014","name":"Ramen Republic","cuisine":"Japanese","address":"456 Post St, San Francisco, CA 94102","phone":"+1-415-555-1014","rating":"4.8","time":"15-25","img":"https://images.unsplash.com/photo-1569718212165-3a8278d5f624?w=400"},
  {"id":"rest_015","name":"Mediterranean Grill","cuisine":"Turkish","address":"1234 Polk St, San Francisco, CA 94109","phone":"+1-415-555-1015","rating":"4.7","time":"30-40","img":"https://images.unsplash.com/photo-1529042410759-befb1204b468?w=400"},
  {"id":"rest_016","name":"Dim Sum Delight","cuisine":"Chinese","address":"567 Broadway, San Francisco, CA 94133","phone":"+1-415-555-1016","rating":"4.6","time":"25-35","img":"https://images.unsplash.com/photo-1563245372-f21724e3856d?w=400"},
  {"id":"rest_017","name":"Pasta Perfecto","cuisine":"Italian","address":"789 Columbus Ave, San Francisco, CA 94133","phone":"+1-415-555-1017","rating":"4.5","time":"30-40","img":"https://images.unsplash.com/photo-1621996346565-e3dbc646d9a9?w=400"},
  {"id":"rest_018","name":"Saigon Street","cuisine":"Vietnamese","address":"2345 Ocean Ave, San Francisco, CA 94127","phone":"+1-415-555-1018","rating":"4.7","time":"20-30","img":"https://images.unsplash.com/photo-1559314809-0d155014e29e?w=400"},
  {"id":"rest_019","name":"Tandoori Nights","cuisine":"Indian","address":"890 Market St, San Francisco, CA 94102","phone":"+1-415-555-1019","rating":"4.9","time":"25-35","img":"https://images.unsplash.com/photo-1585937421612-70a008356fbe?w=400"},
  {"id":"rest_020","name":"Sushi Zen","cuisine":"Japanese","address":"1567 Noriega St, San Francisco, CA 94122","phone":"+1-415-555-1020","rating":"4.8","time":"20-30","img":"https://images.unsplash.com/photo-1579584425555-c3ce17fd4351?w=400"}
]'

echo "$restaurants" | jq -c '.[]' | while read -r restaurant; do
  id=$(echo "$restaurant" | jq -r '.id')
  name=$(echo "$restaurant" | jq -r '.name')
  cuisine=$(echo "$restaurant" | jq -r '.cuisine')
  address=$(echo "$restaurant" | jq -r '.address')
  phone=$(echo "$restaurant" | jq -r '.phone')
  rating=$(echo "$restaurant" | jq -r '.rating')
  time=$(echo "$restaurant" | jq -r '.time')
  img=$(echo "$restaurant" | jq -r '.img')

  aws dynamodb put-item --table-name $TABLE_NAME --region $REGION --item "{
    \"restaurant_id\": {\"S\": \"$id\"},
    \"name\": {\"S\": \"$name\"},
    \"cuisine\": {\"S\": \"$cuisine\"},
    \"address\": {\"S\": \"$address\"},
    \"phone\": {\"S\": \"$phone\"},
    \"avg_rating\": {\"N\": \"$rating\"},
    \"delivery_time\": {\"S\": \"$time\"},
    \"image_url\": {\"S\": \"$img\"},
    \"is_active\": {\"BOOL\": true}
  }" > /dev/null 2>&1

  echo "✓ Added: $name ($cuisine)"
done

echo ""
echo "Successfully seeded 20 restaurants!"
echo ""
echo "Verifying data..."
aws dynamodb scan --table-name $TABLE_NAME --region $REGION --select COUNT
