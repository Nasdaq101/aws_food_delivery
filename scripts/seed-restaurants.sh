#!/bin/bash

# Script to seed the restaurants table with sample data

REGION=${AWS_REGION:-us-west-1}
TABLE_NAME="FoodDelivery-Restaurants"

echo "Seeding restaurants table with 35 restaurants across the Bay Area..."

restaurants='[
  {"id":"rest_001","name":"The Spice Route","cuisine":"Indian","address":"245 Castro St, San Francisco, CA 94114","phone":"+1-415-555-1001","rating":"4.8","time":"25-35","img":"https://images.unsplash.com/photo-1585937421612-70a008356fbe?w=400"},
  {"id":"rest_002","name":"Noodle Nirvana","cuisine":"Thai","address":"1523 Fillmore St, San Francisco, CA 94115","phone":"+1-415-555-1002","rating":"4.6","time":"30-40","img":"https://images.unsplash.com/photo-1569718212165-3a8278d5f624?w=400"},
  {"id":"rest_003","name":"Mediterranean Breeze","cuisine":"Greek","address":"3458 Geary Blvd, San Francisco, CA 94118","phone":"+1-415-555-1003","rating":"4.5","time":"35-45","img":"https://images.unsplash.com/photo-1544025162-d76694265947?w=400"},
  {"id":"rest_004","name":"Seoul Kitchen","cuisine":"Korean","address":"789 Clement St, San Francisco, CA 94118","phone":"+1-415-555-1004","rating":"4.9","time":"20-30","img":"https://images.unsplash.com/photo-1498654896293-37aacf113fd9?w=400"},
  {"id":"rest_005","name":"La Famiglia Trattoria","cuisine":"Italian","address":"2134 Chestnut St, San Francisco, CA 94123","phone":"+1-415-555-1005","rating":"4.7","time":"25-35","img":"https://images.unsplash.com/photo-1595295333158-4742f28fbd85?w=400"},
  {"id":"rest_006","name":"Dragon Palace","cuisine":"Chinese","address":"876 Grant Ave, San Francisco, CA 94108","phone":"+1-415-555-1006","rating":"4.4","time":"30-40","img":"https://images.unsplash.com/photo-1525755662778-989d0524087e?w=400"},
  {"id":"rest_007","name":"Pho Heaven","cuisine":"Vietnamese","address":"1456 Irving St, San Francisco, CA 94122","phone":"+1-415-555-1007","rating":"4.8","time":"20-30","img":"https://images.unsplash.com/photo-1555126634-323283e090fa?w=400"},
  {"id":"rest_008","name":"El Mariachi","cuisine":"Mexican","address":"567 Valencia St, San Francisco, CA 94110","phone":"+1-415-555-1008","rating":"4.3","time":"25-35","img":"https://images.unsplash.com/photo-1613514785940-daed07799d9b?w=400"},
  {"id":"rest_009","name":"Tokyo Express","cuisine":"Japanese","address":"2345 Lombard St, San Francisco, CA 94123","phone":"+1-415-555-1009","rating":"4.7","time":"15-25","img":"https://images.unsplash.com/photo-1553621042-f6e147245754?w=400"},
  {"id":"rest_010","name":"Le Petit Bistro","cuisine":"French","address":"987 Union St, San Francisco, CA 94133","phone":"+1-415-555-1010","rating":"4.9","time":"40-50","img":"https://images.unsplash.com/photo-1559339352-11d035aa65de?w=400"},
  {"id":"rest_011","name":"BBQ Masters","cuisine":"American","address":"3421 Divisadero St, San Francisco, CA 94123","phone":"+1-415-555-1011","rating":"4.6","time":"30-40","img":"https://images.unsplash.com/photo-1544025162-d76694265947?w=400"},
  {"id":"rest_012","name":"Tapas Barcelona","cuisine":"Spanish","address":"1678 Haight St, San Francisco, CA 94117","phone":"+1-415-555-1012","rating":"4.5","time":"25-35","img":"https://images.unsplash.com/photo-1534080564583-6be75777b70a?w=400"},
  {"id":"rest_013","name":"Curry Express","cuisine":"Indian","address":"2890 Mission St, San Francisco, CA 94110","phone":"+1-415-555-1013","rating":"4.4","time":"20-30","img":"https://images.unsplash.com/photo-1574484284002-952d92456975?w=400"},
  {"id":"rest_014","name":"Ramen Republic","cuisine":"Japanese","address":"456 Post St, San Francisco, CA 94102","phone":"+1-415-555-1014","rating":"4.8","time":"15-25","img":"https://images.unsplash.com/photo-1569718212165-3a8278d5f624?w=400"},
  {"id":"rest_015","name":"Mediterranean Grill","cuisine":"Turkish","address":"1234 Polk St, San Francisco, CA 94109","phone":"+1-415-555-1015","rating":"4.7","time":"30-40","img":"https://images.unsplash.com/photo-1529042410759-befb1204b468?w=400"},
  {"id":"rest_016","name":"Dim Sum Delight","cuisine":"Chinese","address":"567 Broadway, San Francisco, CA 94133","phone":"+1-415-555-1016","rating":"4.6","time":"25-35","img":"https://images.unsplash.com/photo-1563245372-f21724e3856d?w=400"},
  {"id":"rest_017","name":"Pasta Perfecto","cuisine":"Italian","address":"789 Columbus Ave, San Francisco, CA 94133","phone":"+1-415-555-1017","rating":"4.5","time":"30-40","img":"https://images.unsplash.com/photo-1621996346565-e3dbc646d9a9?w=400"},
  {"id":"rest_018","name":"Saigon Street","cuisine":"Vietnamese","address":"2345 Ocean Ave, San Francisco, CA 94127","phone":"+1-415-555-1018","rating":"4.7","time":"20-30","img":"https://images.unsplash.com/photo-1559314809-0d155014e29e?w=400"},
  {"id":"rest_019","name":"Tandoori Nights","cuisine":"Indian","address":"890 Market St, San Francisco, CA 94102","phone":"+1-415-555-1019","rating":"4.9","time":"25-35","img":"https://images.unsplash.com/photo-1585937421612-70a008356fbe?w=400"},
  {"id":"rest_020","name":"Sushi Zen","cuisine":"Japanese","address":"1567 Noriega St, San Francisco, CA 94122","phone":"+1-415-555-1020","rating":"4.8","time":"20-30","img":"https://images.unsplash.com/photo-1579584425555-c3ce17fd4351?w=400"},
  {"id":"rest_021","name":"Oakland Soul Food","cuisine":"American","address":"3725 Telegraph Ave, Oakland, CA 94609","phone":"+1-510-555-2001","rating":"4.7","time":"25-35","img":"https://images.unsplash.com/photo-1544025162-d76694265947?w=400"},
  {"id":"rest_022","name":"Lake Merritt Bistro","cuisine":"French","address":"345 Grand Ave, Oakland, CA 94610","phone":"+1-510-555-2002","rating":"4.8","time":"30-40","img":"https://images.unsplash.com/photo-1559339352-11d035aa65de?w=400"},
  {"id":"rest_023","name":"Fruitvale Tacos","cuisine":"Mexican","address":"3400 International Blvd, Oakland, CA 94601","phone":"+1-510-555-2003","rating":"4.5","time":"20-30","img":"https://images.unsplash.com/photo-1613514785940-daed07799d9b?w=400"},
  {"id":"rest_024","name":"Berkeley Bowl Kitchen","cuisine":"American","address":"2020 Oregon St, Berkeley, CA 94703","phone":"+1-510-555-2004","rating":"4.6","time":"25-35","img":"https://images.unsplash.com/photo-1540189549336-e6e99c3679fe?w=400"},
  {"id":"rest_025","name":"Telegraph Curry House","cuisine":"Indian","address":"2556 Telegraph Ave, Berkeley, CA 94704","phone":"+1-510-555-2005","rating":"4.9","time":"30-40","img":"https://images.unsplash.com/photo-1585937421612-70a008356fbe?w=400"},
  {"id":"rest_026","name":"San Pedro Square Grill","cuisine":"American","address":"87 N San Pedro St, San Jose, CA 95110","phone":"+1-408-555-3001","rating":"4.7","time":"25-35","img":"https://images.unsplash.com/photo-1555939594-58d7cb561ad1?w=400"},
  {"id":"rest_027","name":"Japantown Ramen","cuisine":"Japanese","address":"565 N 6th St, San Jose, CA 95112","phone":"+1-408-555-3002","rating":"4.8","time":"20-30","img":"https://images.unsplash.com/photo-1569718212165-3a8278d5f624?w=400"},
  {"id":"rest_028","name":"Santana Row Italian","cuisine":"Italian","address":"377 Santana Row, San Jose, CA 95128","phone":"+1-408-555-3003","rating":"4.6","time":"30-40","img":"https://images.unsplash.com/photo-1595295333158-4742f28fbd85?w=400"},
  {"id":"rest_029","name":"University Avenue Cafe","cuisine":"American","address":"450 University Ave, Palo Alto, CA 94301","phone":"+1-650-555-4001","rating":"4.5","time":"20-30","img":"https://images.unsplash.com/photo-1517248135467-4c7edcad34c4?w=400"},
  {"id":"rest_030","name":"Palo Alto Dim Sum","cuisine":"Chinese","address":"261 University Ave, Palo Alto, CA 94301","phone":"+1-650-555-4002","rating":"4.7","time":"25-35","img":"https://images.unsplash.com/photo-1563245372-f21724e3856d?w=400"},
  {"id":"rest_031","name":"Castro Street Pho","cuisine":"Vietnamese","address":"152 Castro St, Mountain View, CA 94041","phone":"+1-650-555-5001","rating":"4.8","time":"20-30","img":"https://images.unsplash.com/photo-1555126634-323283e090fa?w=400"},
  {"id":"rest_032","name":"Mountain View Thai","cuisine":"Thai","address":"570 N Shoreline Blvd, Mountain View, CA 94043","phone":"+1-650-555-5002","rating":"4.6","time":"25-35","img":"https://images.unsplash.com/photo-1569718212165-3a8278d5f624?w=400"},
  {"id":"rest_033","name":"Fremont Fusion","cuisine":"Korean","address":"39200 Paseo Padre Pkwy, Fremont, CA 94538","phone":"+1-510-555-6001","rating":"4.7","time":"30-40","img":"https://images.unsplash.com/photo-1498654896293-37aacf113fd9?w=400"},
  {"id":"rest_034","name":"San Mateo Sushi Bar","cuisine":"Japanese","address":"42 E 3rd Ave, San Mateo, CA 94401","phone":"+1-650-555-7001","rating":"4.9","time":"20-30","img":"https://images.unsplash.com/photo-1579584425555-c3ce17fd4351?w=400"},
  {"id":"rest_035","name":"Redwood City Pizza","cuisine":"Italian","address":"2001 Broadway, Redwood City, CA 94063","phone":"+1-650-555-8001","rating":"4.5","time":"25-35","img":"https://images.unsplash.com/photo-1574071318508-1cdbab80d002?w=400"}
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
echo "Successfully seeded 35 restaurants across the Bay Area!"
echo "  - San Francisco: 20 restaurants"
echo "  - Oakland: 3 restaurants"
echo "  - Berkeley: 2 restaurants"
echo "  - San Jose: 3 restaurants"
echo "  - Palo Alto: 2 restaurants"
echo "  - Mountain View: 2 restaurants"
echo "  - Fremont: 1 restaurant"
echo "  - San Mateo: 1 restaurant"
echo "  - Redwood City: 1 restaurant"
echo ""
echo "Verifying data..."
aws dynamodb scan --table-name $TABLE_NAME --region $REGION --select COUNT
