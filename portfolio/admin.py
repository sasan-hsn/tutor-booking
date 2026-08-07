from django.contrib import admin
from .models import TeacherProfile, Certificate


class CertificateInline(admin.TabularInline):
    model = Certificate
    extra = 1


class TeacherProfileAdmin(admin.ModelAdmin):
    inlines = [CertificateInline]


admin.site.register(TeacherProfile, TeacherProfileAdmin)