//<script type="text/javascript" src="./DOM.js"></script>


var DOM=new Object();
DOM.MainJS=null;
DOM.List={};
function Text(fn) {
    return fn.toString().split('\n').slice(1,-1).join('\n') + '\n'
}

if($("#DOM").html()!=null){

}
if($("#DOM").html()==null){
	jQuery.getScript("/src/main.js", function(data, status, jqxhr) { 
		DOM.MainJS=data;
		DOM.DOMainJs();
	});
	$("body").append("<buttn id=\"DOM\" class=\"stateVector\" data-reactid=\".0.1.2.2\" style=\"background-color:#9FAA83;position: absolute;right:10px;buttom:10px;\" ><span data-reactid=\".0.1.2.2.0\">Mode</span></button>");
	$("#DOM").click(function(){
		DOM.MainList();
	});
}
DOM.MainList=function(){
	if($("#DOM_Maindiv").html()==null){
	var Maindiv="<div id=\"DOM_Maindiv\" style=\"position: absolute;top: 50%;left: 50%;width: 250px;height:400px;margin:-200px auto auto -125px;background-color:#9FAA83;\" class=\"panel\" ></div>"
	$("body").append(Maindiv);
	var MainHeadText="<div align=\"center\" style=\"position: absolute;top: 0px;left:0px;width:250px;\">MOD设置</div>"
	$("#DOM_Maindiv").append(MainHeadText);
	var MainClose="<div id=\"DOM_MainClose\" style=\"position: absolute;top: 0px;right:0px;background-color:#99CCCC;width:15px;height:15px;\">X</div>"
	$("#DOM_Maindiv").append(MainClose);
	$("#DOM_MainClose").click(function(){$("#DOM_Maindiv").remove(); });
	DOM.addButton("DOMa1","地牢冒险模式",function(){DOM.GetMainJs()});
	DOM.addButton("DOMa2","运行测试事件",function(){DOM.testdom()});
	DOM.addButton("DOMsaverun","保存并加载MOD",function(){DOM.SaveRun()});
	}
}
DOM.GetMainJs=function(){
	if(DOM.MainJS!=null){
				//BUILDING_DATA["dododo"]={name:'机枪塔',desc:'可以dododo怪物。',require:{},timeNeed:0,};
				BUILDING_INIT["build"]={own:false};
				ITEM_DATA["zhangp"]={name:'帐篷',type:'tool',desc:"只要你不怕鬼,可以搭坟前的帐篷。",}
				BOX_INIT["bag"]["things"]["zhangp"]=1;
				BUILDING_DATA["ytdfm"]={name:'爷爷的坟墓',desc:'爷爷的坟墓',require:{},timeNeed:0,};
				BUILDING_INIT["ytdfm"]={own:true};
				BOX_INIT["ytdfm"]={things:{},size:1,isDone:true,};
				//BOX_INIT["dododo"]={things:{},size:1,isDone:true,};
		alert("确认修改请点保存并修改DOM")
	}
		
}
DOM.SaveRun=function(){
	if(DOM.MainJS!=null){
		babel.run(DOM.MainJS);
		$("#DOM_Maindiv").remove();
		alert("加载DOM执行完毕");
	}
	
}
DOM.DOMainJs=function(){
	function getname(fname){
	var fa=fname.indexOf("=");
	fname=fname.slice(fname.lastIndexOf("var",fa)+3,fa)
	return fname.replace(/(^\s*)|(\s*$)/g, "")	
	}
	DOM.List={};
	f=0;
	DOM.num=0
	while(f!=-1){
	var start=DOM.MainJS.indexOf("React.createClass",f+"React.createClass".length+1);
	f=parseInt(start);
		if(start!=-1){
	DOM.num=DOM.num+1;
	var name=DOM.MainJS.slice(DOM.MainJS.lastIndexOf("var",start),start)
	name=getname(name)
	var nextstart=DOM.MainJS.indexOf("React.createClass",parseInt(start)+"React.createClass".length);
	DOM.List[name]={};
	DOM.List[name]["start"]=start;
	DOM.List[name]["end"]=nextstart;
		}
	}
	console.log("DOM加载完成,一共"+DOM.num+"个DOM对象");
	
}
DOM.newMainJs=function(){
		jQuery.getScript("/src/main.js", function(data, status, jqxhr) { 
			DOM.MainJS=data;
			alert("已经重置游戏框架!");
		});	
}
DOM.testdom=function(){
	var yeyedefm=Text(function(){/*
		var yeyedefm = React.createClass({
  		render: function() {
    		var fancyClass = this.props.checked ? 'FancyChecked' : 'FancyUnchecked';
   		 return (
     		 <div className={fancyClass} onClick={this.props.onClick}>
        		{this.props.children}
     		 </div>
   		 );
 			 }
		});
	*/})
	DOM.readdn("HomeComponent","出门","离开墓地")
	DOM.endaddn("BuildingComponent","<TrapComponent/>,","ytdfm        :<yeyedefm/>,");
	DOM.endaddn("ItemComponent","});",yeyedefm);
	
	alert("测试事件完成!")
}
DOM.addn=function(dom,key,code){
	function instr(str,num,code){
  		var str1=str.substr(0,num)
  		var str2=str.substr(num,str.length-num)
  		return str1+code+str2;
		}
	var start =DOM.MainJS.indexOf(key,DOM.List[dom]["start"])
	if(start>parseInt(DOM.List[dom]["end"]))
	{
		return ;
		}
	DOM.MainJS=instr(DOM.MainJS,start,code);
	DOM.DOMainJs();
	alert(DOM.MainJS.substr(start,400));
}
DOM.readdn=function(dom,key,code){
	function instr(str,num,code,keyl){
  		var str1=str.substr(0,num)
  		var str2=str.substr(num+keyl.length,str.length-num)
  		return str1+code+str2;
		}
	var start =DOM.MainJS.indexOf(key,DOM.List[dom]["start"])
	if(start>parseInt(DOM.List[dom]["end"]))
	{
		return ;
		}
	DOM.MainJS=instr(DOM.MainJS,start,code,key);
	DOM.DOMainJs();
	alert(DOM.MainJS.substr(start,400));
}
DOM.endaddn=function(dom,key,code){
	function instr(str,num,code,keyl){
  		var str1=str.substr(0,num+keyl.length)
  		var str2=str.substr(num+keyl.length,str.length-num)
  		return str1+code+str2;
		}
	var start =DOM.MainJS.indexOf(key,DOM.List[dom]["start"])
	if(start>parseInt(DOM.List[dom]["end"]))
	{
		return ;
		}
	DOM.MainJS=instr(DOM.MainJS,start,code,key);
	DOM.DOMainJs();
	alert(DOM.MainJS.substr(start,400));
}
DOM.addButton=function(id,name,fun){
	$("#DOM_Maindiv").append("<buttn id='"+id+"'  class=\"stateVector\"  style=\"background-color:#9F7783;width:90%;;margin:20px auto auto 12.5px; \" ><span>"+name+"</span></button>");
	$("#"+id).click(function(){fun();});
}

