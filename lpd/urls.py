# -*- coding: utf-8 -*-
from django.conf.urls import url

from . import views

app_name = 'lpd'

urlpatterns = [
    url(r'^$', views.ShowOrCreateLearnerProfileDashboardView.as_view(), name='home'),
    url(r'^add$', views.CreateLearnerProfileDashboardView.as_view(), name='add'),
    url(r'^list$', views.ListLearnerProfileDashboardView.as_view(), name='list'),
    url(r'^(?P<pk>\d+)$', views.ShowLearnerProfileDashboardView.as_view(), name='view'),
    url(r'^(?P<pk>\d+)/edit$', views.UpdateLearnerProfileDashboardView.as_view(), name='edit'),
]