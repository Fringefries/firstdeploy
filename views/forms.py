from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth import authenticate

User = get_user_model()

class LoginForm(AuthenticationForm):
    def confirm_login_allowed(self, user):
        pass
    
class AdminAuthForm(forms.Form):
	keep_me_logged = forms.BooleanField()
	username = forms.CharField(label='Username')
	password = forms.CharField(label='Password', widget=forms.PasswordInput)
 
	def clean(self):
		cleaned_data = super().clean()
		if self.is_valid():
			username = self.cleaned_data['username']
			password = self.cleaned_data['password']
			user = authenticate(username=username, password=password)

			if not user:
				raise forms.ValidationError("Invalid login")
			
		return cleaned_data

	def __init__(self,*args,**kwargs):
		super (AdminAuthForm,self ).__init__(*args,**kwargs)
		self.fields['username'].widget.attrs['class'] = 'form-control'
		self.fields['username'].widget.attrs['placeholder'] = 'Username'
		self.fields['password'].widget.attrs['class'] = 'form-control'
		self.fields['password'].widget.attrs['placeholder'] = 'Password'
		self.fields['keep_me_logged'].widget.attrs['class'] = 'new-control-input'
		self.fields['keep_me_logged'].required = False