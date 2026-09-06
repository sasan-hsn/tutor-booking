from portfolio.models import TeacherProfile

def global_teacher_context(request):
    """Make the primary teacher accessible across all templates."""
    teacher = TeacherProfile.objects.select_related('user').first()
    return {
        'default_teacher': teacher,
    }