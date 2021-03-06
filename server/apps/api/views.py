from bs4 import BeautifulSoup
import datetime
from dateutil.parser import parse
from django.db.models import Avg, Max, Min
from django.utils import timezone
from filters.mixins import FiltersMixin
import json
import random
from random import sample
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework_extensions.mixins import NestedViewSetMixin
from rest_framework.permissions import IsAuthenticatedOrReadOnly
import requests
from statistics import mode

from .exceptions import S3FileError
from .globalweather import get_global_weather_city_name
from .models import Clothes, ClothesSet, ClothesSetReview, User, Weather, CategoryData
from .permissions import UserPermissions
from .serializers import (
    ClothesSerializer,
    ClothesSetSerializer,
    ClothesSetReadSerializer,
    ClothesSetReviewSerializer,
    ClothesSetReviewReadSerializer,
    UserSerializer,
    CategoryDataSerializer
)
from .utils import *
from .validations import (
    user_query_schema, 
    clothes_query_schema, 
    clothes_set_query_schema, 
    clothes_set_review_query_schema
)
from .weather import (
    convert_time,
    get_weather_date, 
    get_weather_between, 
    get_weather_time_date, 
    get_current_weather
)

class UserView(FiltersMixin, NestedViewSetMixin, viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    
    # Apply ordering, uses `ordering` query parameter.
    filter_backends = (filters.OrderingFilter, )
    ordering_fields = ('id', 'date_joined', )
    ordering = ('id', )
    
    # Apply filtering, using other query parameters.
    filter_mappings = {
        'gender': 'gender',
        'min_age': 'birthday__lte',
        'max_age': 'birthday__gte',
    }
    
    # TODO(mskwon1) : change this to a more reasonable calculation.
    filter_value_transformations = {
        'min_age' : lambda val: timezone.now() - datetime.timedelta(days=int(val)*365),
        'max_age' : lambda val: timezone.now() - datetime.timedelta(days=int(val)*365),
    }
    
    # Use filter validation.
    filter_validation_schema = user_query_schema
    
    # Permissions.
    permission_classes = [UserPermissions]

    def update(self, request, *args, **kwargs):
        user = request.user
        if user.id != int(kwargs.pop('pk')):
            return Response({
                'error' : 'you are not allowed to access this object'
            }, status=status.HTTP_401_UNAUTHORIZED)
            
        return super().update(request, *args, **kwargs)
    
    def destroy(self, request, *args, **kwargs):
        user = request.user
        if user.id != int(kwargs.pop('pk')):
            return Response({
                'error' : 'you are not allowed to access this object'
            }, status=status.HTTP_401_UNAUTHORIZED)
            
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=['get'])
    def me(self, request, *args, **kwargs):
        """
        A endpoint where current user's data is returned,
        uses Authorization JWT token to check the user.
        """
        if self.request.user.is_authenticated:
            self.kwargs.update(pk=request.user.id)
            return self.retrieve(request, *args, **kwargs)
        else:
            return Response(
                {'error': 'please log in'},
                status=status.HTTP_401_UNAUTHORIZED)

class CategoryDataView(FiltersMixin, NestedViewSetMixin, viewsets.ModelViewSet):
    queryset = CategoryData.objects.all()
    serializer_class = CategoryDataSerializer

    @action(detail=False, methods=['get'])
    def category(self, request, *args, **kwargs):
        category_set = CategoryData.objects.all().filter(id=request.query_params.get('category_id'))
        category_id = category_set.values_list('id', flat=True)[0]
        upper_category = category_set.values_list('upper_category', flat=True)[0]
        lower_category = category_set.values_list('lower_category', flat=True)[0]

        return Response({
            "category_id" : category_id,
            "upper_category" : upper_category,
            "lower_category" : lower_category
        })
        
    @action(detail=False, methods=['get'])   
    def filter_category(self, request, *args, **kwargs):
        
        category_id_set = CategoryData.objects.all()
        if request.query_params.get('upper_category'):
            category_id_set = category_id_set.filter(upper_category=request.query_params.get('upper_category'))

        if request.query_params.get('lower_category'):
            category_id_set = category_id_set.filter(lower_category=request.query_params.get('lower_category'))

        category_id_set = category_id_set.values_list('id', flat=True)        
        filtered_clothes_set = Clothes.objects.all().filter(category__in=category_id_set, owner_id=request.user.id).values()   

        return Response(filtered_clothes_set)


class ClothesView(FiltersMixin, NestedViewSetMixin, viewsets.ModelViewSet):
    queryset = Clothes.objects.all()
    serializer_class = ClothesSerializer
    
    def get_queryset(self):
        queryset = Clothes.objects.all()
        
        # me 파라미터가 true인 경우, 해당 유저의 옷만 반환.
        if self.request.query_params.get('me'):
            user = self.request.user
            queryset = queryset.filter(owner=user.id)
                
        return queryset

    # Apply ordering, uses `ordering` query parameter.
    filter_backends = (filters.OrderingFilter, )
    ordering_fields = ('created_at', 'id', )
    ordering = ('-created_at', )

    # Use filter validation.
    filter_validation_schema = clothes_query_schema
    
    # Permissions.
    permission_classes = [IsAuthenticatedOrReadOnly]
    
    def list(self, request, *args, **kwargs):
        # If me parameter is set, check authentication.
        if request.query_params.get('me') and not request.user.is_authenticated:
            return Response({
                'error': 'token authorization failed ... please log in'
            }, status=status.HTTP_401_UNAUTHORIZED)
            
        return super().list(request, *args, **kwargs)
    
    def create(self, request, *args, **kwargs):
        # Move image from temp to saved on s3 storage.
        if 'image_url' in request.data.keys():
            image_url = request.data['image_url']
            try:
                request.data['image_url']  = move_image_to_saved(image_url, 'clothes')
            except S3FileError:
                return Response({
                    'error': 'image does not exist ... plesase try again'
                }, status=status.HTTP_400_BAD_REQUEST)
        
        return super(ClothesView, self).create(request, *args, **kwargs)  
    
    def perform_create(self, serializer):
        serializer.save(owner_id = self.request.user.id)      

    def update(self, request, *args, **kwargs):
        user = request.user
        key = int(kwargs.pop('pk'))
        target_clothes = Clothes.objects.filter(id=key)
        
        if user.id != int(target_clothes[0].owner.id):
            return Response({
                'error' : 'you are not allowed to access this object'
            }, status=status.HTTP_401_UNAUTHORIZED)
            
        return super().update(request, *args, **kwargs)
    
    def destroy(self, request, *args, **kwargs):
        user = request.user
        key = int(kwargs.pop('pk'))
        target_clothes = Clothes.objects.filter(id=key)
        
        if user.id != int(target_clothes[0].owner.id):
            return Response({
                'error' : 'you are not allowed to access this object'
            }, status=status.HTTP_401_UNAUTHORIZED)
   
        return super().destroy(request, *args, **kwargs)
 
    @action(detail=False, methods=['post'])
    def inference(self, request, *args, **kwargs):
        """
        An endpoint where the analysis of a clothes is returned
        """
        image = byte_to_image(request.data['image'])
        image_tensor = image_to_tensor(image)
        inference_result = execute_inference(image_tensor)
        upper, lower = get_categories_from_predictions(inference_result)
        category_id = CategoryData.objects.all().filter(upper_category=upper, lower_category=lower).values('id')
        image = remove_background(image)
        image_url = save_image_s3(image, 'clothes')
        
        return Response({'image_url': image_url, 
                         'upper_category':upper, 
                         'lower_category':lower,
                         'category_id':category_id
                         }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'])
    def today_category(self, request, *args, **kwargs):
        """
        An endpoint where the today_category is returned
        """
        
        MAX_ITEM_NUM = 3
        MAX_IMAGE_NUM = 3

        min_temp = float(request.query_params.get('minTemp'))
        max_temp = float(request.query_params.get('maxTemp'))
        wind_speed = float(request.query_params.get('windSpeed'))
        humidity = float(request.query_params.get('humidity'))
        
        weather_type = get_weather_class([max_temp, min_temp, wind_speed, humidity])
        # 모든 사용자 중 (날씨 적절성 3 + 현재와 유사한 날씨)인 코디 리뷰 추출
        filtered_cody_review_set = ClothesSetReview.objects.all().filter(review=3, weather_type=weather_type)

        # 추출된 코디 리뷰의 코디 id 저장
        filtered_clothes_set_id = []
        # flat=True : dict 형식으로 변환
        filtered_clothes_set_id.append(filtered_cody_review_set.values_list('clothes_set', flat=True))
        
        # id로 필터링 된 코디 query set
        filtered_clothes_set = ClothesSet.objects.filter(pk__in=filtered_clothes_set_id)
        
        combination_dict = {}
        for clothes_set in filtered_clothes_set:
            
            # 한 코디에 대한 각 옷들의 하위 카테고리 추출
            category_set = CategoryData.objects.all().filter(id__in=clothes_set.clothes.values_list('category', flat=True))
            comb = tuple(category_set.values_list('lower_category', flat=True).order_by('lower_category').distinct())
            if comb in combination_dict.keys():
                combination_dict[comb][0] += 1
                combination_dict[comb][1].add(clothes_set.image_url)
            else:
                combination_dict[comb] = [1,set([clothes_set.image_url])]
        
        result = sorted(combination_dict.items(), key=(lambda x:x[1][0]), reverse=True)[:MAX_ITEM_NUM]

        result_list = []
        for item in result:
            item = list(item)
            result_dict = {}
            result_dict['combination'] = '-'.join(list(item[0]))
            images_list = list(item[1][1])

            if len(images_list) > MAX_IMAGE_NUM:
                images_list = sample(images_list, MAX_IMAGE_NUM)

            result_dict['images'] = images_list
            result_list.append(result_dict)

        return Response(result_list)

    
    @action(detail=False, methods=['get'])
    def lookbook(self, request, *args, **kwargs):
        """
        An endpoint where the lookbook is returned
        """

        # 페이지에서 보여줄 패션 스타일 이미지 갯수
        img_num = 5
        year = str(datetime.datetime.now().year)
        user_gender = 'm' if request.user.gender == 'M' else 'f'
        url = ''.join(['https://www.musinsa.com/index.php?m=shopstaff&_y=', year, '&ordw=d_regis&gender=', user_gender])
        
        div_tag = BeautifulSoup(requests.get(url).text, 'html.parser').find('div', class_='list-box box')

        # img_num 만큼 0~ran_max 랜덤 숫자 뽑기(반복 x)
        ran_max = 9
        num_list = []
        ran_num = random.randint(0, ran_max)

        for i in range(img_num):
            while ran_num in num_list:
                ran_num = random.randint(0, ran_max)
            num_list.append(ran_num)

        li_tag = div_tag.find_all('li', class_='listItem')
        
        lookbook_list = []
        try:
            for count in range(img_num):
                ran_li = li_tag[int(num_list[count])]
                # 이미지 url 받아오기
                img_url = ran_li.find('img').get('src')
                # 브랜드명 받아오기
                brand = ran_li.find('p', class_='brackets brand').text
                # 모델 이름 받아오기
                name = ran_li.find('span').text
                lookbook_list.append({'image' : img_url, 'brand' : brand, 'name' : name})
        
        # url 변경 등의 문제로 크롤링 오류 발생 시 예외 처리 
        except:
            return Response({
                    'error' : 'internal server error'
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(lookbook_list)


class ClothesNestedView(FiltersMixin, NestedViewSetMixin, viewsets.ModelViewSet):
    queryset = Clothes.objects.all()
    serializer_class = ClothesSerializer  
    
    # Apply ordering, uses `ordering` query parameter.
    filter_backends = (filters.OrderingFilter, )
    ordering_fields = ('created_at', 'id', )
    ordering = ('-created_at', )

    # Use filter validation.
    filter_validation_schema = clothes_query_schema
    
    # Permissions.
    permission_classes = [IsAuthenticatedOrReadOnly]


class ClothesSetView(FiltersMixin, NestedViewSetMixin, viewsets.ModelViewSet):
    def get_queryset(self):
        queryset = ClothesSet.objects.all()
        
        # me 파라미터가 true인 경우, 해당 유저의 코디만 반환
        if self.request.query_params.get('me'):
            user = self.request.user
            queryset = queryset.filter(owner=user.id)
                
        return queryset
    
    def get_serializer_class(self):
        if self.action == 'create' or self.action == 'update':
            return ClothesSetSerializer
        return ClothesSetReadSerializer 

    # Apply ordering, uses `ordering` query parameter.
    filter_backends = (filters.OrderingFilter, )
    ordering_fields = ('created_at', 'id', )
    ordering = ('-created_at', )

    # Apply filtering, using other query parameters.
    filter_mappings = {
        'style': 'style',
    }

    # Use filter validation.
    filter_validation_schema = clothes_set_query_schema
    
    # Permissions.
    permission_classes = [IsAuthenticatedOrReadOnly]
    
    def list(self, request, *args, **kwargs):
        # If me parameter is set, check authentication.
        if request.query_params.get('me') and not request.user.is_authenticated:
            return Response({
                'error' : 'token authorization failed ... please log in'
            }, status=status.HTTP_401_UNAUTHORIZED)
            
        queryset = self.filter_queryset(self.get_queryset())
        if request.query_params.get('review'):
            reviews = ClothesSetReview.objects.all()
        
            for clothesSet in list(queryset):
                if len(reviews.filter(clothes_set__id=clothesSet.id)) == 0:
                    queryset = queryset.exclude(id=clothesSet.id)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
            
        return Response(serializer.data)
            
    
    # 요청된 이미지를 s3에 저장 후 url 반환
    def get_image_url(req_image):
        image = byte_to_image(req_image)
        temp_url = save_image_s3(image, 'clothes-sets')
        image_url = move_image_to_saved(temp_url, 'clothes-sets')

        return (image_url)


    def create(self, request, *args, **kwargs):
        if 'clothes' in request.data.keys():
            filtered_clothes = Clothes.objects.all().filter(owner_id=request.user.id)
            filtered_clothes_id = []
            for clothes in filtered_clothes:
                filtered_clothes_id.append(int(clothes.id))
                
            # 입력된 옷들이 모두 해당 유저의 것인지 확인.
            for clothes_id in request.data['clothes']:
                if int(clothes_id) not in filtered_clothes_id:
                    return Response({
                        "error" : "this is not your clothes : " + clothes_id
                    }, status=status.HTTP_200_OK)
        
        if 'image' in request.data.keys():
            # 해당 image의 url           
            request.data['image_url'] = ClothesSetView.get_image_url(request.data['image'])
        
        else:
            return Response({
                "error": 'image field is required'
            }, status=status.HTTP_400_BAD_REQUEST)
            
        return super().create(request, *args, **kwargs)
    
    def perform_create(self, serializer):
        serializer.save(owner_id = self.request.user.id)

    def update(self, request, *args, **kwargs):
        target_clothes_set = ClothesSet.objects.filter(id=int(kwargs.pop('pk')))
        
        if request.user.id != int(target_clothes_set[0].owner.id):
            return Response({
                'error' : 'you are not allowed to access this object'
            }, status=status.HTTP_401_UNAUTHORIZED)
            
        if 'image' in request.data.keys():
            # 해당 image의 url             
            request.data['image_url'] = ClothesSetView.get_image_url(request.data['image'])
            
        return super().update(request, *args, **kwargs)
    
    def destroy(self, request, *args, **kwargs):
        target_clothes_set = ClothesSet.objects.filter(id=int(kwargs.pop('pk')))
        
        if request.user.id != int(target_clothes_set[0].owner.id):
            return Response({
                'error' : 'you are not allowed to access this object'
            }, status=status.HTTP_401_UNAUTHORIZED)
            
        return super().destroy(request, *args, **kwargs)
    
    
class ClothesSetNestedView(FiltersMixin, NestedViewSetMixin, viewsets.ModelViewSet):
    queryset = ClothesSet.objects.all()
    serializer_class = ClothesSetReadSerializer  
    
    # Apply ordering, uses `ordering` query parameter.
    filter_backends = (filters.OrderingFilter, )
    ordering_fields = ('created_at', 'id', )
    ordering = ('-created_at', )

    # Apply filtering, using other query parameters.
    filter_mappings = {
        'style': 'style',
    }

    # Use filter validation.
    filter_validation_schema = clothes_set_query_schema
    
    # Permissions.
    permission_classes = [IsAuthenticatedOrReadOnly]
        
        
class ClothesSetReviewView(FiltersMixin, NestedViewSetMixin, viewsets.ModelViewSet):    
    def get_queryset(self):
        queryset = ClothesSetReview.objects.all()
        
        # me 파라미터가 true인 경우, 해당 유저의 Review만 반환
        if self.request.query_params.get('me'):
            user = self.request.user
            queryset = queryset.filter(owner=user.id)
                
        return queryset

    def  get_serializer_class(self):
        if self.action == 'create' or self.action == 'update':
            return ClothesSetReviewSerializer
        return ClothesSetReviewReadSerializer

    # Apply ordering, uses `ordering` query parameter.
    filter_backends = (filters.OrderingFilter, )
    ordering_fields = ('created_at', 'id', )
    ordering = ('-created_at', )

    # Apply filtering, using other query parameters.
    filter_mappings = {
        'start_datetime': 'start_datetime__gte',
        'end_datetime': 'end_datetime__lte',
        'location' : 'location',
        'review': 'review',
    }

    # Use filter validation.
    filter_validation_schema = clothes_set_review_query_schema
    
    # Permissions.
    permission_classes = [IsAuthenticatedOrReadOnly]    
    
    def list(self, request, *args, **kwargs):
        # If me parameter is set, check authentication.
        if request.query_params.get('me') and not request.user.is_authenticated:
            return Response({
                'error' : 'token authorization failed ... please log in'
            }, status=status.HTTP_401_UNAUTHORIZED)
            
        queryset = self.filter_queryset(self.get_queryset())

        max_temp = float(request.query_params.get('maxTemp'))
        min_temp = float(request.query_params.get('minTemp'))
        wind_speed = float(request.query_params.get('windSpeed'))
        humidity = float(request.query_params.get('humidity'))
        
        weather_type = get_weather_class([max_temp, min_temp, wind_speed, humidity])
        queryset = queryset.filter(weather_type=weather_type)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        
        return Response(serializer.data)

    # 외출 시작~끝 날짜, 시간 변환
    def conv_date_time(start, end, location):
        start_date = start.split('T')[0]
        start_time = start.split('T')[1].split(':')
        end_date = end.split('T')[0]
        end_time = end.split('T')[1].split(':')

        # 외출 시작 시간, 시작 년/달/일 -> 변환된 시간, 날짜
        start_conv_time, start_conv_date = convert_time(start_time[0] + start_time[1], start_date.split('-')[0], start_date.split('-')[1], start_date.split('-')[2])
        start_conv_time = int(start_conv_time[0] + start_conv_time[1])
        start_conv_date = start_conv_date[:4] + '-' + start_conv_date[4:6] + '-' + start_conv_date[6:]

        # 외출 끝 시간, 끝 년/달/일 -> 변환된 시간, 날짜
        end_conv_time, end_conv_date = convert_time(end_time[0] + end_time[1], end_date.split('-')[0], end_date.split('-')[1], end_date.split('-')[2])
        end_conv_time = int(end_conv_time[0] + end_conv_time[1])
        end_conv_date = end_conv_date[:4] + '-' + end_conv_date[4:6] + '-' + end_conv_date[6:]

        return (start_conv_date, end_conv_date, start_conv_time, end_conv_time)

    # 외출 시작~끝에 해당하는 날씨 수집
    def req_weather_api(location, start_conv_date, end_conv_date, start_conv_time, end_conv_time):
        # 지역, 시작~끝 날짜에 따른 날씨 필터링
        weather_data_set = Weather.objects.all().filter(location_code=location).exclude(date__lt=start_conv_date, date__gt=end_conv_date)
        weather_data_on_start = weather_data_set.exclude(date=start_conv_date, time__lt=start_conv_time)
        weather_data_on_end = weather_data_on_start.exclude(date=end_conv_date, time__gt=end_conv_time)

        return (weather_data_on_end)

    # 날씨 DB에 날씨 정보 수집 후 저장
    def common_weather_create(start, end, location, weather_data_on_end):
        with open('apps/api/locations/data.json') as json_file:
            json_data = json.load(json_file)
                    
            new_x = int((json_data[str(location)]['x']))
            new_y = int((json_data[str(location)]['y']))    
            date_list = [start, end]

            for date_time in date_list:
                date = date_time.strftime('%Y-%m-%d %H:%M:%S')
                # 시간, 년, 월, 일 -> 변환된 시간, 날짜
                conv_time, conv_date = convert_time(date[1].split(':')[0] + date[1].split(':')[1], date[0].split('-')[0], date[0].split('-')[1], date[0].split('-')[2])
            
                try:
                    response = get_weather_date(date, str(location))
                except:
                    return Response({
                        'error' : 'internal server error'
                    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                            
                weather_data_on_end.objects.create(location_code=location, date=date[0:10], time=conv_time[0:2], x=new_x, y=new_y,
                                                    temp=response['T3H'], sensible_temp=response['WCI'], humidity=response['REH'], 
                                                    wind_speed=response['WSD'], precipitation=response['R06'])

    
    def create(self, request, *args, **kwargs):
        if 'clothes_set' in request.data:
            filtered_clothes_set = ClothesSet.objects.all().filter(owner_id=request.user.id)
            filtered_clothes_set_id = []
            for clothes_set in filtered_clothes_set:
                filtered_clothes_set_id.append(int(clothes_set.id))
                
            # 입력된 코디가 해당 유저의 것인지 확인.
            if int(request.data['clothes_set']) not in filtered_clothes_set_id:
                return Response({
                    "error" : "this is not your clothes set : " + request.data['clothes_set']
                }, status=status.HTTP_200_OK)
        
        if set(['clothes_set', 'start_datetime', 'end_datetime', 'location', 'review']).issubset(request.data.keys()):
            start = request.data['start_datetime']
            end = request.data['end_datetime']
            location = int(request.data['location'])

            # 외출 시작~끝 날짜, 시간 변환
            start_conv_date, end_conv_date, start_conv_time, end_conv_time = ClothesSetReviewView.conv_date_time(start, end, location)
            # 외출 시작~끝에 해당하는 날씨 수집을 위한 api 요청
            weather_data_on_end = ClothesSetReviewView.req_weather_api(location, start_conv_date, end_conv_date, start_conv_time, end_conv_time)
            
            # 해당 날씨 정보가 없을 때
            if weather_data_on_end.count()==0:
                today = datetime.datetime.now() - datetime.timedelta(hours=24)

                if parse(start) < today:
                    return Response({
                        'error' : 'internal server error'
                    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

                else:
                    # 날씨 DB에 날씨 정보 수집 후 저장
                    ClothesSetReviewView.common_weather_create(start, end, location, weather_data_on_end)
                    
                                                                  
            request.data['max_temp'] = weather_data_on_end.aggregate(Max('temp'))['temp__max']
            request.data['min_temp'] = weather_data_on_end.aggregate(Min('temp'))['temp__min']
            request.data['max_sensible_temp'] = weather_data_on_end.aggregate(Max('sensible_temp'))['sensible_temp__max']
            request.data['min_sensible_temp'] = weather_data_on_end.aggregate(Min('sensible_temp'))['sensible_temp__min']
            request.data['humidity'] = weather_data_on_end.aggregate(Avg('humidity'))['humidity__avg']
            request.data['wind_speed'] = weather_data_on_end.aggregate(Avg('wind_speed'))['wind_speed__avg']
            request.data['precipitation'] = weather_data_on_end.aggregate(Avg('precipitation'))['precipitation__avg']
            
            request.data['weather_type'] = get_weather_class([
                request.data['max_temp'],
                request.data['min_temp'],
                request.data['wind_speed'],
                request.data['humidity'],
            ])
        
        return super(ClothesSetReviewView, self).create(request, *args, **kwargs)
    

    def perform_create(self, serializer):
        serializer.save(owner_id=self.request.user.id)

    def update(self, request, *args, **kwargs):
        target_clothes_review_set = ClothesSetReview.objects.filter(id=int(kwargs.pop('pk')))
        
        if request.user.id != int(target_clothes_review_set[0].owner.id):
            return Response({
                'error' : 'you are not allowed to access this object'
            }, status=status.HTTP_401_UNAUTHORIZED)
        
        if set(['clothes_set', 'start_datetime', 'end_datetime', 'location', 'review']).issubset(request.data.keys()):
            start = request.data['start_datetime']
            end = request.data['end_datetime']
            location = int(request.data['location'])
            
            # 외출 시작~끝 날짜, 시간 변환
            start_conv_date, end_conv_date, start_conv_time, end_conv_time = ClothesSetReviewView.conv_date_time(start, end, location)

            # 외출 시작~끝에 해당하는 날씨 수집을 위한 api 요청
            weather_data_on_end = ClothesSetReviewView.req_weather_api(location, start_conv_date, end_conv_date, start_conv_time, end_conv_time)
            
            # 해당 날씨 정보가 없을 때
            if weather_data_on_end.count()==0:
                today = datetime.datetime.now() - datetime.timedelta(hours=24)

                if parse(start) < today:
                    return Response({
                        'error' : 'internal server error'
                    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

                else:
                    # 날씨 DB에 날씨 정보 수집 후 저장
                    ClothesSetReviewView.common_review_create(start, end, location, weather_data_on_end)
        
          
            request.data['max_temp'] = weather_data_on_end.aggregate(Max('temp'))['temp__max']
            request.data['min_temp'] = weather_data_on_end.aggregate(Min('temp'))['temp__min']
            request.data['max_sensible_temp'] = weather_data_on_end.aggregate(Max('sensible_temp'))['sensible_temp__max']
            request.data['min_sensible_temp'] = weather_data_on_end.aggregate(Min('sensible_temp'))['sensible_temp__min']
            request.data['humidity'] = weather_data_on_end.aggregate(Avg('humidity'))['humidity__avg']
            request.data['wind_speed'] = weather_data_on_end.aggregate(Avg('wind_speed'))['wind_speed__avg']
            request.data['precipitation'] = weather_data_on_end.aggregate(Avg('precipitation'))['precipitation__avg']
            
            request.data['weather_type'] = get_weather_class([
                request.data['max_temp'],
                request.data['min_temp'],
                request.data['wind_speed'],
                request.data['humidity'],
            ])
        
        return super(ClothesSetReviewView, self).update(request, *args, **kwargs)
    
    def destroy(self, request, *args, **kwargs):
        target_clothes_review_set = ClothesSetReview.objects.filter(id=int(kwargs.pop('pk')))
        
        if request.user.id != int(target_clothes_review_set[0].owner.id):
            return Response({
                'error' : 'you are not allowed to access this object'
            }, status=status.HTTP_401_UNAUTHORIZED)

        return super().destroy(request, *args, **kwargs)    
    
    @action(detail=False, methods=['get'])
    def location_search(self, request, *args, **kwargs):
        """
        An endpoint that returns search result for
        location based on query parameter
        """
        
        # Get query parameters.
        search = request.query_params.get('search')
        search = '' if search == None else search
        limit = request.query_params.get('limit')
        offset = request.query_params.get('offset')
        
        # Open JSON file for location results.
        with open('apps/api/locations/data.json') as json_file:
            data = json.load(json_file)
            
        # Get results total count & initial list containing search keyword.    
        results = []
        count = 0
        for index in data:
            if search in data[index]['full_address']:
                count += 1
                results.append({
                    'id' : index,
                    'location' : data[index]['full_address']
                })
        
        # Filter list according to limit & offset.
        final_results = []
        offset = 0 if offset == None else int(offset)
        limit = count if limit == None else int(limit)

        limit_count = 0        
        for result in results[offset:]:
            limit_count += 1
            final_results.append(result)
            if limit_count == limit:
                break
        
        # Return response.
        return Response({
                'count': count,
                'next': offset + limit_count,
                'results': final_results,
            }, status=status.HTTP_200_OK)

    # 국내 현재 날씨/해외 예보 날씨 공통 파라미터 제공
    def common_weather_api(weather_data):
        max_temp = float(weather_data['MAX'])
        min_temp = float(weather_data['MIN'])
        humidity = int(weather_data['REH'])
        wind_speed = float(weather_data['WSD'])
        sense = float(weather_data['WCI'])
        max_sense = float(weather_data['WCIMAX'])
        min_sense = float(weather_data['WCIMIN'])

        return (max_temp, min_temp, humidity, wind_speed, sense, max_sense, min_sense)
    

    @action(detail=False, methods=['get'])
    def global_weather(self, request, *args, **kwargs):
        """
        An endpoint that returns global weather data for 
        location and date which wanna forecast
        """
        # Get Location.
        city_name = request.query_params.get('city_name')
        forecast_date = request.query_params.get('date')
        weather_data = get_global_weather_city_name(forecast_date, city_name)
        temperature = float(weather_data['TEMP'])
        precipitation = float(weather_data['PRE'])
        # 날씨 정보 수집
        max_temp, min_temp, humidity, wind_speed, sense, max_sense, min_sense = ClothesSetReviewView.common_weather_api(weather_data)

        return Response({
        'temperature': temperature,
        'min_temperature': min_temp,
        'max_temperature': max_temp,
        'chill_temp': sense,
        'min_chill_temp': min_sense,
        'max_chill_temp': max_sense,
        'humidity': humidity,
        'wind_speed': wind_speed,
        'precipitation': precipitation,
            }, status=status.HTTP_200_OK)
        
    @action(detail=False, methods=['get'])
    def global_search(self, request, *args, **kwargs):
        """
        An endpoint that returns search result for
        location based on query parameter
        """
        
        # Get query parameters.
        search = request.query_params.get('search')
        search = '' if search == None else search
        limit = request.query_params.get('limit')
        offset = request.query_params.get('offset')
        
        # Open JSON file for location results.
        with open('apps/api/locations/cities_20000.json', 'rt', encoding='UTF-8') as json_file:
            data = json.load(json_file)
            
        # Get results total count & initial list containing search keyword.    
        results = []
        count = 0
        for city in data:
            if search in city['city_name']:
                count += 1
                results.append({
                    'id': city['city_id'],
                    'location' : city['city_name']
                })
        
        # Filter list according to limit & offset.
        final_results = []
        offset = 0 if offset == None else int(offset)
        limit = count if limit == None else int(limit)

        limit_count = 0        
        for result in results[offset:]:
            limit_count += 1
            final_results.append(result)
            if limit_count == limit:
                break
        
        # Return response.
        return Response({
                'count': count,
                'next': offset + limit_count,
                'results': final_results,
            }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'])
    def current_weather(self, request, *args, **kwargs):
        """
        An endpoint that returns weather data for
        location based on query parameter and current time
        """
        # Get Location.
        location = request.query_params.get('location')
        weather_data = get_current_weather(location)
        temperature = float(weather_data['T1H'])
        precipitation = float(weather_data['RN1'])
        # 날씨 정보 수집
        max_temp, min_temp, humidity, wind_speed, sense, max_sense, min_sense = ClothesSetReviewView.common_weather_api(weather_data)

        # Return response
        return Response({
                'temperature': temperature,
                'min_temperature': min_temp,
                'max_temperature': max_temp,
                'chill_temp': sense,
                'min_chill_temp': min_sense,
                'max_chill_temp': max_sense,
                'humidity': humidity,
                'wind_speed': wind_speed,
                'precipitation': precipitation,
            }, status=status.HTTP_200_OK)

class ClothesSetReviewNestedView(FiltersMixin, NestedViewSetMixin, viewsets.ModelViewSet):
    queryset = ClothesSetReview.objects.all()
    serializer_class = ClothesSetReviewReadSerializer  

    # Apply ordering, uses `ordering` query parameter.
    filter_backends = (filters.OrderingFilter, )
    ordering_fields = ('created_at', 'id', )
    ordering = ('-created_at', )

    # Apply filtering, using other query parameters.
    filter_mappings = {
        'start_datetime': 'start_datetime',
        'end_datetime': 'end_datetime',
        'location' : 'location',
        'max_sensible_temp' : 'max_sensible_temp',
        'min_sensible_temp ' : 'min_sensible_temp',
    }

    # Use filter validation.
    filter_validation_schema = clothes_set_review_query_schema
    
    # Permissions.
    permission_classes = [IsAuthenticatedOrReadOnly]   
